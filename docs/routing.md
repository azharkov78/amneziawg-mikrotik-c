# Маршрутизация, DNS и MTU

> Раздел документации [awg-proxy](../README.md): контейнер RouterOS, который подключает MikroTik к серверам AmneziaWG. Проще всего ставить [конфигуратором](https://timbrs.github.io/amneziawg-mikrotik-c/conf3.0.html): он сам генерирует все команды. Остальные разделы: [установка](install.md) · [обновление](update.md) · [переменные](env.md) · [AWG 3.x и IPv6](protocol.md) · [маршрутизация и MTU](routing.md) · [серверный режим](server-mode.md) · [неполадки](troubleshooting.md) · [FAQ](faq.md).

## Маршрутизация трафика через туннель

Конкретный хост:

```routeros
/ip/route/add dst-address=8.8.8.8/32 gateway=wg-awg-proxy
```

Подсеть:

```routeros
/ip/route/add dst-address=10.0.0.0/8 gateway=wg-awg-proxy
```

Просмотр маршрутов:

```routeros
/ip/route/print where gateway=wg-awg-proxy
```

Удаление маршрута:

```routeros
/ip/route/remove [find where dst-address="8.8.8.8/32" gateway="wg-awg-proxy"]
```

## DNS через туннель

Чтобы DNS-запросы шли через туннель, укажите DNS-сервер и добавьте маршрут к нему:

```routeros
/ip/dns/set servers=8.8.8.8,8.8.4.4
/ip/route/add dst-address=8.8.8.8/32 gateway=wg-awg-proxy
/ip/route/add dst-address=8.8.4.4/32 gateway=wg-awg-proxy
```

## MTU: PPPoE и MTU из конфига

Внешняя UDP-датаграмма считается так:

```
IP (20 для IPv4, 40 для IPv6) + UDP 8 + S4 + WG-заголовок 16 + MTU + тег 16
```

При стандартном MTU 1420 и `S4 = 0` это 1484 байта — влезает в 1500. Но два
случая ломают этот расчёт:

- **PPPoE.** Канал даёт не 1500, а 1492. При `S4 = 12` наружу уходит 1496 байт,
  и каждый полноразмерный пакет едет двумя IP-фрагментами. `change-mss` тут не
  спасает: он про TCP, а переполняется внешняя UDP-датаграмма — задевая в том
  числе QUIC, на котором работают YouTube и Google Play. В конфигураторе есть
  поле **«MTU нижележащего канала»**: поставьте 1492 для PPPoE (меньше — для
  PPPoE поверх VLAN), и MTU туннеля будет посчитан от него.
- **`MTU` в самом `.conf`.** Если сервер прислал `MTU = 1300`, это значение
  теперь переносится на WireGuard-интерфейс. Расчётный потолок по `S4` может
  только опустить его ещё ниже, но не поднять обратно.

**Прокси сам подсказывает нужный MTU.** Пакеты к серверу уходят с флагом DF
(«не фрагментировать»). Если где-то на пути канал уже пакета (PPPoE, LTE, чужой
туннель), узел перед этим участком пакет отбрасывает и отвечает ICMP «fragmentation
needed» (по IPv6 — «packet too big») с допустимым размером. Ядро запоминает этот
path MTU и дальше само режет крупные пакеты на фрагменты: туннель работает, но
скорость проседает. Прокси раз в 5 секунд спрашивает path MTU у ядра и пишет в
лог:

```
INFO: remote: path MTU to the server is 1492, larger packets are fragmented
WARN: set the WireGuard interface MTU to 1408 or lower: packets to the server are 1496 bytes, the path takes 1492, each one is fragmented
```

Число в `WARN` — готовое значение для WireGuard-интерфейса роутера, посчитанное
по формуле выше от настоящего path MTU и вашего `S4`:

```routeros
/interface/wireguard/set [find name=wg-awg-proxy] mtu=1408
```

- `WARN` появляется, только если в путь не пролезают пакеты с данными. Если в
  логе есть только строка `path MTU`, крупным был пакет перед рукопожатием
  (junk или `I1`–`I5`). Он и так мусорный, на скорость не влияет, менять ничего
  не нужно.
- Ядро забывает path MTU через 10 минут и узнаёт его заново. Одно и то же
  значение прокси пишет в лог один раз.
- Если ICMP по дороге режут, ядро path MTU не узнает, и крупные пакеты будут
  просто теряться. `WARN` тогда появится, только если пакет больше 1500 байт.
  Path MTU можно найти вручную: `/ping <IP сервера> size=1492 do-not-fragment`,
  уменьшая `size`, пока пинг не пройдёт. Наибольший прошедший `size` и есть
  path MTU.
- С `AWG_NO_DF=1` по IPv4 узлы на пути режут пакеты на фрагменты молча, без
  ICMP. Строки `path MTU` не будет, а просадка останется.

В версиях до 1.4.2 включительно прокси принимал этот ICMP за обрыв: в логе было
`remote read error (Message too large), will reconnect`, связь пропадала
примерно на секунду, и менялся исходный порт.

## DNS-маршрутизация: что она может и чего не может

Маршрутизация по сервисам работает так: клиент резолвит домен **через роутер**,
RouterOS кладёт полученный адрес в address-list, mangle помечает соединения к
этому адресу. Отсюда два ограничения, о которых стоит знать заранее:

- **Клиент должен спрашивать DNS у роутера.** Устройство с DoH, Android Private
  DNS, прошитым `8.8.8.8` или просто с адресом в своём кэше не создаёт запроса,
  который роутер бы увидел — его трафик пойдёт мимо туннеля. Проверить:
  открыть сервис и посмотреть `/ip/firewall/address-list/print where list=YouTube`.
  Пустой список означает, что проблема в DNS, а не в AWG.
- **Часть сервисов ходит по IP без DNS вообще.** Telegram (MTProto) подключается
  прямо к своим диапазонам, поэтому они прописываются в список статически —
  одними доменами его не поймать.
- **Исключения в режиме «весь не-РФ трафик» работают так же.** Правило
  `action=accept` срабатывает только для адресов, которые роутер уже положил в
  список исключения (`Steam`, `EpicGames` и т.д.). Всё, чего в списке нет —
  другой CDN, адрес из кэша клиента, игровые серверы по прямому IP, — уходит в
  туннель. Проверить: `/ip/firewall/address-list/print where list=Steam`, а
  узнать, куда идёт конкретное соединение, — `/ip/firewall/connection/print
  where dst-address~"1.2.3."` (метка `connection-mark=…-non-ru` значит, что оно в
  туннеле). Уже открытые соединения после правки списка не переедут — их нужно
  сбросить: `/ip/firewall/connection/remove [find where connection-mark=awg-proxy-1-non-ru]`.

## Короткий TTL у CDN

У сервисов за Cloudflare TTL бывает в несколько секунд. Адрес успевает выпасть
из address-list, пока браузер ещё держит соединение, и следующее соединение
уходит мимо туннеля. Галочка **«Держать записи DNS address-list дольше TTL»**
задаёт `/ip/dns address-list-extra-time` (по умолчанию 30m).

Настройка общая на весь роутер, а не на конкретный список, поэтому:

- она применяется, **только** если там всё ещё дефолтные `0s` — чужое значение
  скрипт не трогает и пишет об этом в вывод;
- скрипт удаления сбрасывает её в `0s`, **только** если значение осталось ровно
  тем, что поставил конфигуратор.

Слишком большое значение держит устаревшие адреса CDN и может увести в туннель
лишнее, поэтому 10–30 минут — разумный потолок.

## Маршрутизация по address-list (продвинутое)

Для выборочной маршрутизации трафика через туннель используйте routing table и mangle rules.

Создание routing table:

```routeros
/routing/table/add disabled=no fib name=r_to_vpn
```

Маршрут по умолчанию через туннель для этой таблицы:

```routeros
/ip/route/add dst-address=0.0.0.0/0 gateway=wg-awg-proxy routing-table=r_to_vpn
```

Address-list с адресами, которые нужно направить через туннель:

```routeros
/ip/firewall/address-list/add address=8.8.8.8 list=to_vpn
/ip/firewall/address-list/add address=1.1.1.1 list=to_vpn
```

Mangle rules для маркировки трафика:

```routeros
# Пропускаем локальный трафик
/ip/firewall/mangle/add chain=prerouting action=accept dst-address=10.0.0.0/8
/ip/firewall/mangle/add chain=prerouting action=accept dst-address=172.16.0.0/12
/ip/firewall/mangle/add chain=prerouting action=accept dst-address=192.168.0.0/16

# Маркируем соединения к адресам из списка
/ip/firewall/mangle/add chain=prerouting action=mark-connection \
    dst-address-list=to_vpn connection-mark=no-mark \
    new-connection-mark=to-vpn-conn passthrough=yes

# Маркируем маршрутизацию для отмеченных соединений
/ip/firewall/mangle/add chain=prerouting action=mark-routing \
    connection-mark=to-vpn-conn new-routing-mark=r_to_vpn passthrough=yes
```

NAT для маркированного трафика:

```routeros
/ip/firewall/nat/add chain=srcnat action=masquerade routing-mark=r_to_vpn
```

Теперь весь трафик к адресам из списка `to_vpn` будет идти через туннель. Добавляйте адреса в список по мере необходимости.
