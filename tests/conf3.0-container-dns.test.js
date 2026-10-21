/* Свойство dns контейнера: без него RouterOS 7.24 не запускает контейнер на
 * роутере, у которого нет своих DNS-серверов. Гоняется через jsdom.
 *
 * Что проверено на hAP ax² с 7.24.4 (серверы роутера убраны, остался DoH):
 *   - dns не задан   -> "start failed: no default dns servers, explicitly set
 *                       empty dns override if intended", контейнер стоит;
 *   - dns=""         -> стартует, resolv.conf RouterOS не трогает вовсе;
 *   - dns=1.1.1.1    -> стартует, в resolv.conf этот адрес;
 *   - dns=<роутер на veth> -> стартует, но резолв не идёт: штатный фаервол
 *                       режет DNS-запросы не из LAN;
 *   - dns=8.8.8.8 и AWG_DNS=1.1.1.1 -> прокси после старта сам переписывает
 *                       resolv.conf, в нём 1.1.1.1. AWG_DNS всегда главнее.
 *
 * Отсюда правило и то, что здесь закреплено:
 *   - установка отдаёт контейнеру AWG_DNS, а без него (endpoint - IP-адрес)
 *     пустой override. Безусловно: иначе роутер, позже переведённый на DoH,
 *     после перезагрузки остался бы без туннеля;
 *   - пересборка в <prefix>-update ставит пустой override, только когда у
 *     контейнера пуст default-dns (DNS роутера нет): ей может достаться
 *     старая установка с доменом без AWG_DNS, которая живёт на DNS роутера;
 *   - на 7.20-7.23 ничего не меняется: свойство идёт через [:parse] внутри
 *     :do/on-error и только при $minor >= 24.
 *
 * Только для разработки: у самого прокси зависимостей нет, а здесь нужна одна.
 *   npm install jsdom && node tests/conf3.0-container-dns.test.js
 * Возвращает ненулевой код, если что-то сломано.
 */
const fs = require('fs');
const path = require('path');
const { JSDOM } = require('jsdom');

const NL = String.fromCharCode(10);
const html = fs.readFileSync(path.join(__dirname, '..', 'docs', 'conf3.0.html'), 'utf8');

let fails = 0, passes = 0;
function ok(name, cond, extra) {
    if (cond) { passes++; console.log('  PASS  ' + name); }
    else { fails++; console.log('  FAIL  ' + name + (extra ? ': ' + extra : '')); }
}

const netCalls = [];
function offlineGuard(win) {
    const boom = name => function () {
        netCalls.push(name);
        throw new Error('test tried to use the network via ' + name);
    };
    for (const name of ['fetch', 'XMLHttpRequest', 'WebSocket', 'EventSource']) {
        try { win[name] = boom(name); } catch (e) { /* read-only in some builds */ }
    }
    try { win.navigator.sendBeacon = boom('sendBeacon'); } catch (e) { /* ignore */ }
}

const dom = new JSDOM(html, {
    runScripts: 'dangerously',
    url: 'https://example.invalid/',
    beforeParse: offlineGuard,
});
const w = dom.window;

const need = ['buildImageSetupLines', 'buildUpdateScriptSource', 'generate'];
const missing = need.filter(n => typeof w[n] !== 'function');
if (missing.length) {
    console.log('  FAIL  functions not reachable: ' + missing.join(', '));
    process.exit(1);
}

console.log('=== conf3.0 container dns tests ===');

// Блок от ":if ($minor >= 24) do={" до его закрывающей скобки на том же отступе.
function dnsBlocks(script) {
    const lines = script.split(NL);
    const out = [];
    for (let i = 0; i < lines.length; i++) {
        const m = /^(\s*):if \(\$minor >= 24\) do=\{$/.exec(lines[i]);
        if (!m) continue;
        let j = i + 1;
        while (j < lines.length && lines[j] !== m[1] + '}') j++;
        const body = lines.slice(i, j + 1);
        if (body.some(l => l.indexOf('dns=') >= 0)) out.push({ at: i, end: j, lines: body, text: body.join(NL) });
    }
    return out;
}

const P = 'awg-proxy-1';
const SET = '[:parse "/container/set [find where interface=veth-awg-proxy-1] dns=\\"$cDns\\""]';
const READ = ':do { :set cDns [/container/envs/get [find where list="awg-proxy-1-env" key="AWG_DNS"] value] } on-error={}';

/* ------------------------------------------------- 1. установка */

const install = w.buildImageSetupLines(P, 'disk1', {}).join(NL);
const inst = dnsBlocks(install);
ok('установка задаёт контейнеру dns ровно один раз', inst.length === 1, String(inst.length));
const ib = inst[0] || { lines: [], text: '', at: -1, end: -1 };

ok('значение берётся из AWG_DNS этой установки', ib.text.indexOf(READ) >= 0, ib.text);
ok('без AWG_DNS остаётся пустая строка - пустой override',
   ib.text.indexOf(':local cDns ""') >= 0 && ib.text.indexOf(SET) >= 0, ib.text);
ok('в установке пустой override безусловный: роутер могут перевести на DoH позже',
   ib.text.indexOf('default-dns') < 0 && ib.text.indexOf('defDns') < 0, ib.text);

const setLines = ib.lines.filter(l => l.indexOf('dns=') >= 0);
ok('dns идёт через :parse внутри :do/on-error - старые RouterOS не сломаются',
   setLines.length > 0 && setLines.every(l => l.indexOf('[:parse') >= 0 && l.indexOf('on-error={}') >= 0),
   setLines.join(' | '));
ok('dns не передаётся аргументом /container/add',
   !/\/container\/add[^\n]*\bdns=/.test(install),
   install.split(NL).filter(l => l.indexOf('/container/add') >= 0).join(' | '));

// Задавать dns после старта бессмысленно: контейнер уже упал бы на старте, а
// /container/set на живом контейнере его перезапускает.
const lines = install.split(NL);
const startAt = lines.findIndex(l => l.indexOf('/container/start') >= 0);
const lastAdd = lines.reduce((acc, l, i) => l.indexOf('/container/add') >= 0 ? i : acc, -1);
ok('dns выставляется после всех /container/add и до запуска',
   ib.at > lastAdd && startAt > ib.end, 'add=' + lastAdd + ' dns=' + ib.at + '..' + ib.end + ' start=' + startAt);

// Адрес роутера на veth не резолвит при штатном фаерволе, а 1.1.1.1 и подобные
// в России могут не отвечать: своего адреса конфигуратор контейнеру не навязывает.
ok('никакого вшитого адреса: только AWG_DNS или пустой override',
   !/dns=\\?"?\d/.test(ib.text), ib.text);

// Внешний диск и автовыбор идут той же функцией - проверить, что блок не теряется.
['usb1', '__auto'].forEach(function (disk) {
    const s = w.buildImageSetupLines(P, disk, {}).join(NL);
    ok('установка на ' + disk + ' тоже задаёт dns', dnsBlocks(s).length === 1);
});

/* ------------------------------------------------- 2. пересборка в <prefix>-update */

const upd = w.buildUpdateScriptSource(P);
const ub = dnsBlocks(upd);
ok('скрипт обновления задаёт dns ровно в одном месте', ub.length === 1, String(ub.length));
const u = ub[0] || { lines: [], text: '', at: -1, end: -1 };
const updLines = upd.split(NL);

const rebuildAt = updLines.findIndex(l => l.indexOf('rebuilding it from the registry') >= 0);
const rebuildAdd = updLines.findIndex((l, i) => i > rebuildAt && l.indexOf('/container/add remote-image=$img') >= 0);
const rebuildStart = updLines.findIndex((l, i) => i > u.end && l.indexOf('/container/start [:pick $newCids') >= 0);
ok('dns задаётся пересобранному контейнеру - после add и до запуска',
   rebuildAt > 0 && rebuildAdd > rebuildAt && u.at > rebuildAdd && rebuildStart > u.end,
   'rebuild=' + rebuildAt + ' add=' + rebuildAdd + ' dns=' + u.at + ' start=' + rebuildStart);

// Обычная ночная проверка трогает живой контейнер только для privileged, и то
// один раз: /container/set его перезапускает.
ok('живому контейнеру dns не переставляется', !/\/container\/set \$cid[^\n]*dns=/.test(upd));

ok('пересборка тоже читает AWG_DNS этой установки', u.text.indexOf(READ) >= 0, u.text);
// Старт падает ровно тогда, когда RouterOS не нашёл для контейнера DNS роутера:
// default-dns пуст. Его и смотрим - /ip/dns скрипт обновления не трогает вовсе.
ok('пустой override только когда у пересобранного контейнера пуст default-dns',
   u.text.indexOf(':local dnsId [:pick $newCids ([:len $newCids] - 1)]') >= 0 &&
   u.text.indexOf('[:parse ":return [/container/get $dnsId default-dns]"]') >= 0 &&
   /:if \(\$cDns != "" \|\| \$defDns = 0\) do=\{ :do \{ \[:parse "\/container\/set /.test(u.text), u.text);
// Если default-dns прочитать не удалось, считать его непустым: лишний пустой
// override сломал бы домен без AWG_DNS, а не задать dns - ничего не меняет.
ok('непрочитанный default-dns считается непустым',
   u.text.indexOf(':local defDns 1') >= 0 && /:do \{ :set defDns [^\n]*\} on-error=\{\}/.test(u.text), u.text);
ok('скрипт обновления не обращается к /ip/dns', upd.indexOf('/ip/dns') < 0);
ok('в пересборке dns тоже через :parse и on-error',
   u.lines.filter(l => l.indexOf('dns=') >= 0).every(l => l.indexOf('[:parse') >= 0 && l.indexOf('on-error={}') >= 0));

// Скрипт обновления подставляется и в старые установки со своими именами veth и
// env list - dns должен идти по ним, а не по выведенным из префикса.
const legacy = w.buildUpdateScriptSource('awg', { veth: 'veth-awg-old', envlist: 'awg-old-env', name: 'awg-update' });
const lb = dnsBlocks(legacy)[0] || { text: '' };
ok('старая установка: dns по её veth и env list',
   lb.text.indexOf('list="awg-old-env" key="AWG_DNS"') >= 0 &&
   lb.text.indexOf('/container/set [find where interface=veth-awg-old] dns=') >= 0, lb.text);

/* ------------------------------------------------- 3. сквозная генерация */

function confWith(endpoint) {
    return [
        '[Interface]',
        'PrivateKey = ' + 'A'.repeat(43) + '=',
        'Address = 10.13.13.2/32',
        'Jc = 4', 'Jmin = 40', 'Jmax = 70',
        'S1 = 30', 'S2 = 40',
        'H1 = 1111111111', 'H2 = 2222222222', 'H3 = 3333333333', 'H4 = 444444444',
        '',
        '[Peer]',
        'PublicKey = ' + 'B'.repeat(43) + '=',
        'Endpoint = ' + endpoint,
        'AllowedIPs = 0.0.0.0/0'
    ].join(NL);
}
function runGenerate(endpoint, dns) {
    w.document.getElementById('awg-dns').value = dns;
    w.document.getElementById('conf-input').value = confWith(endpoint);
    w.document.getElementById('errors-container').innerHTML = '';
    w.document.getElementById('output').dataset.plain = '';
    w.generate();
    return {
        err: w.document.getElementById('errors-container').textContent.trim(),
        out: w.document.getElementById('output').dataset.plain || ''
    };
}

// Значение читается из env list во время выполнения, так что AWG_DNS обязан
// оказаться там раньше, чем до него дойдёт блок dns.
const host = runGenerate('vpn.example.com:443', '192.168.88.1');
ok('домен с AWG_DNS генерируется', host.err.length === 0, host.err);
// Первым в скрипте идёт исходник <prefix>-update: он только сохраняется, а
// выполняется блок самой установки - тот, что без проверки DNS роутера.
const hostEnv = host.out.split(NL).findIndex(l => l.indexOf('key=AWG_DNS value="192.168.88.1"') >= 0);
const hostDns = dnsBlocks(host.out).filter(b => b.text.indexOf('defDns') < 0);
ok('AWG_DNS записан в env list раньше, чем его читает блок dns установки',
   hostEnv >= 0 && hostDns.length === 1 && hostDns[0].at > hostEnv,
   'env=' + hostEnv + ' dns=' + hostDns.map(b => b.at).join(','));

const ip = runGenerate('198.51.100.1:443', '');
ok('IP-адрес без AWG_DNS генерируется', ip.err.length === 0, ip.err);
ok('и всё равно получает блок dns - пустой override', dnsBlocks(ip.out).length >= 1);

w.document.getElementById('awg-dns').value = '';

/* ------------------------------------------------- 4. ничего лишнего */

ok('на страницу не приходит ни одного сетевого вызова', netCalls.length === 0, netCalls.join(', '));

console.log('');
console.log(passes + '/' + (passes + fails) + ' checks passed');
process.exit(fails ? 1 : 0);
