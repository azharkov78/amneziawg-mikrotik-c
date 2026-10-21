# Устранение неполадок

> Раздел документации [awg-proxy](../README.md): контейнер RouterOS, который подключает MikroTik к серверам AmneziaWG. Проще всего ставить [конфигуратором](https://timbrs.github.io/amneziawg-mikrotik-c/conf3.0.html): он сам генерирует все команды. Остальные разделы: [установка](install.md) · [обновление](update.md) · [переменные](env.md) · [AWG 3.x и IPv6](protocol.md) · [маршрутизация и MTU](routing.md) · [серверный режим](server-mode.md) · [неполадки](troubleshooting.md) · [FAQ](faq.md).

**Контейнер не запускается** -- проверьте установку пакета container (`/system/package/print`), режим устройства (`/system/device-mode/print`) и свободное место (`/system/resource/print`).

## execvpe /awg-proxy: No such file or directory

Контейнер запускается, но сразу завершается с ошибкой `exited with status 255: execvpe /awg-proxy: No such file or directory`. Это означает, что бинарник не распаковался — образ скачался некорректно или не полностью.

1. Удалите контейнер и root-dir:
```routeros
/container/stop [find where comment=awg-proxy]
:delay 7s
/container/remove [find where comment=awg-proxy]
/file/remove disk1/awg-proxy
:do { /file/remove [find where name~"awg-proxy.*tar"] } on-error={}
```

2. Заново скачайте образ и проверьте размер файла (`/file/print`) — он должен быть 100-300 КБ, не 0.

3. Пересоздайте контейнер.

## Site-to-site: handshake did not complete

В режиме site-to-site (два MikroTik через AWG proxy) handshake не завершается, хотя контейнеры работают. Типичные причины:

**1. Firewall forward chain на стороне B (сервер)**

DSTNAT-трафик идёт через `forward` chain, а не `input`. Если правило `accept` добавлено в конец цепочки, а выше есть `drop` — пакеты не доходят до контейнера.

Диагностика:
```routeros
/ip/firewall/filter/print where chain=forward
```

Исправление — переместите правило в начало:
```routeros
/ip/firewall/filter/remove [find where comment=PREFIX-awg-in]
/ip/firewall/filter/add chain=forward action=accept protocol=udp dst-port=AWG_PORT in-interface-list=WAN place-before=0 comment=PREFIX-awg-in
```

**2. Firewall input chain на стороне B (сервер)**

В reverse-режиме контейнер инициирует NEW-соединение к WG-порту MikroTik (в отличие от стандартного режима, где MikroTik инициирует → ответ контейнера = established). Если veth-интерфейс не в LAN interface-list, input chain дропает пакеты от контейнера.

Исправление:
```routeros
/ip/firewall/filter/add chain=input action=accept protocol=udp src-address=CONTAINER_IP dst-port=WG_PORT place-before=0 comment=PREFIX-wg-in
```

**3. DNS-резолв AWG_REMOTE на стороне A (клиент)**

Если `AWG_REMOTE` указан как hostname, контейнеру нужен работающий DNS: образ собран из `scratch`, своего `/etc/resolv.conf` в нём нет, и прокси пишет его из `AWG_DNS`. Конфигуратор спрашивает этот адрес полем **«DNS-сервер для контейнера»** и не даёт сгенерировать установку с доменом в endpoint без него. Вручную: установите `AWG_DNS=8.8.8.8` или `AWG_DNS=1.1.1.1` в переменных окружения контейнера. Адрес должен быть доступен из контейнера **без** туннеля — сам роутер или публичный резолвер, но не DNS из конфига, который обычно живёт внутри туннеля. Прокси периодически перепроверяет DNS в фоне (`AWG_DNS_REFRESH`, по умолчанию раз в 60 с) и сам переподключается при смене IP сервера. Если DNS тоже идёт через туннель (замкнутый круг) — разрешите hostname вручную и укажите IP:
```routeros
:put [:resolve vpn.example.com]
# Затем пропишите полученный IP в AWG_REMOTE
```

**4. Диагностика через логи**

Включите debug-логирование на обоих контейнерах:
```routeros
/container/envs/add list=PREFIX-env key=AWG_LOG_LEVEL value=debug
```
Перезапустите контейнеры и проверьте логи — они покажут ошибки DNS-резолва, connect, отправку handshake и junk-пакетов.

**5. Резервный профиль (fallback)**

Конфиги site-to-site и server из конфигуратора содержат основной профиль и цепочку отката. Если основная обфускация блокируется, инициатор через `AWG_FB_AFTER` секунд молчания сервера сам переходит к следующей ступени — в логах видно `fallback: remote silent, trying profile stage N` (инициатор) и `c2s: peer uses a different profile stage, switched` (отвечающая сторона). Стартовая строка `config: fallback chain of N profiles` подтверждает, что цепочка задана. Оба конца должны быть сгенерированы одним конфигуратором, иначе их профили не совпадут.

**Нет рукопожатия** -- убедитесь, что все параметры AWG (Jc, Jmin, Jmax, S1, S2, H1--H4) точно совпадают с сервером. Проверьте `AWG_REMOTE`, `AWG_SERVER_PUB` и `AWG_CLIENT_PUB`. Для диагностики установите `AWG_LOG_LEVEL=debug` -- в логах будет видно отправку handshake init и junk-пакетов. Если в логах `remote: receive failed (Connection refused)` -- сервер недоступен или неправильный порт. На ARM64 попробуйте `AWG_NO_GRO=1` -- если ядро не поддерживает GRO, прокси может зависнуть в ожидании ответа.

**Нет трафика после рукопожатия** -- проверьте правило NAT (`/ip/firewall/nat/print`), маршрутизацию и `endpoint-address` пира (должен быть `172.18.0.2`).

**Контейнер перезапускается** -- установите `AWG_LOG_LEVEL=info` и проверьте логи. Частая причина -- отсутствующие переменные окружения.

## Storage device not found

Если при установке появляется ошибка `Storage device usb1 not found or has 0 free space` -- диск не отформатирован или имя точки монтирования не совпадает.

1. Проверьте доступные диски:

```routeros
/disk/print
```

2. Если диск виден как block-устройство, но без раздела -- отформатируйте его в ext4:

```routeros
/disk/format-drive usb1 file-system=ext4 label=usb1
```

3. После форматирования диск будет доступен как mount-point (обычно `usb1`). Проверьте имя через `/disk/print` и используйте его в конфигураторе (поле "Container storage").

> **Важно:** Контейнеры требуют файловую систему ext4. FAT32 не подходит.

## Insufficient disk space

Если при установке контейнера возникает ошибка `Insufficient disk space`, а на внешнем накопителе (USB, SD, NVMe) есть свободное место -- перенастройте директорию для загрузки образов:

```routeros
/container/config set tmpdir=usb1/pull memory-high=200M
```

Замените `usb1` на mount-point вашего накопителя (см. `/disk/print`).

После установки контейнера можно вернуть значение обратно:

```routeros
/container/config set tmpdir="" memory-high=0
```

Если используете конфигуратор -- выберите нужный накопитель в поле "Container storage", и tmpdir будет настроен автоматически.

## no space to extract layer

Ошибка `download/extract error: no space to extract layer`: RouterOS распаковывает слои образа во временный
каталог (`tmpdir`), и во внутренней памяти на это не хватило места. Чаще всего так бывает на роутерах со 128 МБ
flash без USB (hAP ax², hAP ax S), когда память уже заняли другие контейнеры или копии от прошлых обновлений.

1. Есть USB-накопитель — уведите `tmpdir` на него, как в разделе выше.
2. USB нет — на время установки распакуйте образ в RAM:

   ```routeros
   /disk/add type=tmpfs tmpfs-max-size=32M
   /disk/print
   ```

   Найдите в списке новый диск (обычно `tmpfs1`) и укажите его:

   ```routeros
   /container/config/set tmpdir=tmpfs1/pull
   ```

   После установки верните `tmpdir` (`/container/config/set tmpdir=""`) и удалите tmpfs-диск: он занимает RAM.
3. Удалите каталоги `<root-dir>.backup`, которые остаются после `/container/repull`: RouterOS их не убирает
   (см. [обновление вручную](update.md#вручную)).

## start failed: no default dns servers

На RouterOS 7.24 контейнер не запускается с ошибкой
`start failed: no default dns servers, explicitly set empty dns override if intended`, если у самого роутера
нет DNS-серверов: в `/ip/dns` пусты и `servers`, и `dynamic-servers` (например, настроен только DoH).
RouterOS отдаёт контейнеру серверы роутера — они видны как `default-dns` в `/container/print detail`, — и без
них, если у контейнера не задан свой `dns`, старт обрывается, контейнер остаётся остановленным.

Скрипты из конфигуратора задают `dns` сами: тот же адрес, что в `AWG_DNS`, а если endpoint указан IP-адресом,
пустое значение. Контейнер, поставленный раньше, поправьте руками (подставьте свой comment из
`/container/print`) и запустите:

```routeros
# endpoint - IP-адрес: прокси DNS не нужен
/container/set [find where comment=awg-proxy-1] dns=""
# endpoint - доменное имя: тот же адрес, что в AWG_DNS
/container/set [find where comment=awg-proxy-1] dns=1.1.1.1
/container/start [find where comment=awg-proxy-1]
```

- `dns=""` — тот самый пустой override, о котором говорит ошибка: контейнер стартует, а `/etc/resolv.conf`
  RouterOS ему не пишет. Прокси резолвит только доменное имя из `AWG_REMOTE`, и резолвер он берёт из
  `AWG_DNS`: после старта сам переписывает `/etc/resolv.conf`. Поэтому `AWG_DNS`, если он задан, всегда
  главнее `dns`.
- Адрес самого роутера (на veth или в LAN) в `dns` не годится: штатный фаервол (`drop all not coming from LAN`)
  отбрасывает DNS-запросы с veth — контейнер запустится, но имя не разрешится.
- `/container/set` на работающем контейнере его перезапускает.

## not allowed by device-mode

Ошибка `not allowed by device-mode` возникает в трёх случаях:

- При создании контейнера -- не включена поддержка контейнеров (`container=no`)
- При скачивании образа через `/tool/fetch` -- не включён fetch (`fetch=no`)
- При создании планировщика (`/system/scheduler/add`) -- не включён scheduler (`scheduler=no`); без него RU-список не обновляется автоматически, и записи с timeout молча исчезают через 30 дней

Проверьте текущее состояние:

```routeros
/system/device-mode/print
```

Затем включите нужные возможности:

```routeros
/system/device-mode/update container=yes fetch=yes bandwidth-test=yes scheduler=yes
```

Роутер попросит подтверждение -- нажмите кнопку Reset или Mode на корпусе (зависит от модели) в течение нескольких минут, либо дождитесь автоматической перезагрузки. После перезагрузки повторите установку.

## child spawn failed / could not load next layer

На устройствах с 16 МБ flash (hAP ac2, hEX и др.) контейнер может не запускаться с ошибками:
- `child spawn failed: container run error` или `exited with status 255` (RouterOS 7.20)
- `download/extract error: could not load next layer` (RouterOS 7.21+)

Чек-лист:

1. **Формат образа** -- убедитесь, что используете правильный формат:
   - RouterOS 7.21+: `awg-proxy-{arch}.tar.gz` (OCI)
   - RouterOS 7.20 и ниже: `awg-proxy-{arch}-7.20-Docker.tar.gz` (Docker)

2. **tmpdir на USB** -- без этого RouterOS распаковывает образ на внутреннюю flash, которой не хватает (замените `usb1` на ваш mount-point из `/disk/print`):
   ```routeros
   /container/config set tmpdir=usb1/pull
   ```

3. **root-dir** -- указывайте путь к папке на USB, но **не создавайте её вручную** (RouterOS создаст её сам):
   ```routeros
   /container add ... root-dir=usb1/awg-proxy
   ```

4. **Формат USB** -- отформатируйте накопитель в ext4:
   ```routeros
   /disk/format-drive usb1 file-system=ext4 label=usb1
   ```

5. **Место под слои** -- на устройствах с 16 МБ flash во внутренней памяти обычно нет места
   под распаковку. Либо загружайте образ файлом:
   ```routeros
   /container add file=awg-proxy-arm.tar.gz ...
   ```
   либо, если ставите из реестра (`remote-image`, RouterOS 7.22+), сначала уведите временный
   каталог на USB — слои качаются в `tmpdir`, а не в root-dir:
   ```routeros
   /container/config set tmpdir=usb1/pull
   ```
   Конфигуратор делает это сам, когда контейнер ставится не на `disk1`.

## exited with signal 4 (Illegal instruction)

Контейнер сразу падает с ошибкой:

```
*** error: exited with signal 4 (Illegal instruction)
```

Причина: образ собран для более новой архитектуры CPU, чем у роутера. Типичный случай -- **hEX refresh (E50UG)**, **hEX S 2025 (E60iUGS)** и **hAP ax S (E62iUGS)**: их процессор показывает `architecture-name: arm`, но исполняет только arm32v5-образы ([ограничение RouterOS](https://help.mikrotik.com/docs/spaces/ROS/pages/84901929/Container)), а стандартный `awg-proxy-arm.tar.gz` собран под ARMv7.

Решение -- используйте armv5-образ:

```routeros
/container/remove [find where comment=awg-proxy]
/tool/fetch url="https://github.com/timbrs/amneziawg-mikrotik-c/releases/latest/download/awg-proxy-armv5.tar.gz" dst-path=awg-proxy-armv5.tar.gz
/container/add file=awg-proxy-armv5.tar.gz ... # остальные параметры как раньше
```

Для RouterOS 7.20 и ниже -- `awg-proxy-armv5-7.20-Docker.tar.gz`. Свежие конфиги из конфигуратора определяют эти устройства автоматически.

## Список RU-адресов не загружается после перезагрузки

При использовании сценария "Весь не-РФ трафик через туннель" список RU-адресов может не загружаться автоматически после перезагрузки роутера. Типичные причины:

**1. Scheduler: `start-time=startup` + `interval` несовместимы**

Это документированное поведение RouterOS: если у scheduler задан `interval` отличный от `0`, trigger `start-time=startup` **не срабатывает**. Scheduler покажет `run-count=0` после перезагрузки.

Решение — два отдельных scheduler:

```routeros
# Для запуска при загрузке (interval ОБЯЗАТЕЛЬНО 0)
/system/scheduler/add name=awg-proxy-ru-startup on-event="/system/script/run awg-proxy-ru-update" start-time=startup interval=0 comment=awg-proxy

# Для ежедневного обновления
/system/scheduler/add name=awg-proxy-ru-daily on-event="/system/script/run awg-proxy-ru-update" start-time=04:00:00 interval=1d comment=awg-proxy
```

Если у вас один scheduler с `start-time=startup interval=1d` — удалите его и создайте два.

**2. USB-диск монтируется с задержкой**

USB-накопитель появляется через 5-30 секунд после загрузки. Если скрипт запускается раньше, он не найдёт файл `.rsc` на USB. Конфигуратор добавляет в скрипт ожидание монтирования (до 60 секунд):

```routeros
:if ($disk != "disk1") do={
  :local waited 0
  :while ([:len [/disk/find where mount-point=$disk]] = 0 && $waited < 60) do={
    :delay 5s
    :set waited ($waited + 5)
  }
}
```

**3. Сеть не готова при загрузке**

DHCP и DNS могут быть недоступны в первые секунды после boot. Скрипт ожидает доступность сети (до 60 секунд) перед скачиванием:

```routeros
:local waitNet 0
:while ($waitNet < 60) do={
  :do { :resolve "github.com"; :set waitNet 99 } on-error={ :delay 5s; :set waitNet ($waitNet + 5) }
}
```

Если кешированный `.rsc` файл есть на диске — он импортируется сразу (не дожидаясь сети), и маршрутизация работает с первых секунд. Свежая версия скачивается позже.

**4. Потеря времени при перезагрузке (WG handshake fails)**

Некоторые модели MikroTik (без батарейки RTC) теряют время при перезагрузке. Если часы сильно отстают, WireGuard-сервер отклоняет handshake (защита TAI64N от replay-атак). Пока handshake не пройдёт — туннель не работает и LAN-трафик теряется.

Последовательность при загрузке:
1. Время сбито → WG handshake не проходит → туннель не работает
2. NTP синхронизирует время (~5-15 сек) → время исправлено
3. WG handshake проходит → туннель поднимается
4. USB монтируется (~10-30 сек) → RU-список загружается из кеша

**Важно:** NTP-трафик самого роутера идёт напрямую (не через туннель), т.к. mangle-правила действуют только на `in-interface-list=LAN`. Проблема не в маршрутизации NTP, а в скорости синхронизации.

Рекомендации:

```routeros
# Используйте IP-адреса для NTP (не требуют DNS-резолва при старте)
/system/ntp/client/set enabled=yes servers=216.239.35.0,216.239.35.4,ntp2.vniiftri.ru,ntp.ix.ru

# Добавьте NTP и DNS серверы в RU list (для LAN-устройств, чтобы их NTP тоже шёл напрямую)
/ip/firewall/address-list/add list=RU address=216.239.35.0 comment=awg-proxy-ntp
/ip/firewall/address-list/add list=RU address=216.239.35.4 comment=awg-proxy-ntp
/ip/firewall/address-list/add list=RU address=8.8.8.8 comment=awg-proxy-dns
/ip/firewall/address-list/add list=RU address=8.8.4.4 comment=awg-proxy-dns
```

Окно без интернета для LAN ~10-20 секунд (до синхронизации NTP + WG handshake). NTP time-jump также может сбить планирование scheduler.

## Handshake не проходит после восстановления из бэкапа

После восстановления RouterOS из бэкапа (backup/restore) WireGuard handshake не завершается, хотя контейнеры работают, veth-интерфейсы в состоянии running и пинг до контейнера проходит. В логах:

```
wg-awg-proxy: [peer] ...: Handshake for peer did not complete after 5 seconds, retrying (try 2)
```

**Причина:** восстановление из бэкапа сбрасывает системные часы на дату создания бэкапа. WireGuard использует TAI64N timestamp в handshake init для защиты от replay-атак — сервер запоминает последний timestamp каждого пира и отбрасывает handshake с более старым временем. Если часы роутера отстают от реального времени, сервер молча игнорирует все handshake-пакеты.

**Диагностика:**

```routeros
/system/clock/print
# Если дата не совпадает с текущей — это причина
```

**Исправление:**

1. Установите правильное время:
```routeros
/system/clock/set date=apr/05/2026 time=12:00:00
```

2. Включите NTP-клиент для автоматической синхронизации:
```routeros
/system/ntp/client/set enabled=yes servers=time.google.com,pool.ntp.org
```

3. Перезапустите контейнеры и сбросьте WG-пиры для принудительного нового handshake:
```routeros
/container/stop [find]
:delay 5s
/container/start [find]
# Сбросить пиры (disable/enable)
/interface/wireguard/peers/set [find where !disabled] disabled=yes
:delay 2s
/interface/wireguard/peers/set [find where disabled] disabled=no
```

> **Совет:** После каждого восстановления из бэкапа первым делом проверяйте системные часы (`/system/clock/print`).

## Строка `GRO not coalescing` в логе

`INFO: c2s: GRO not coalescing, falling back to recvmmsg` (или `s2c: …`) — не ошибка. Ядро не склеивает
входящие пакеты в пачки, и прокси переходит на обычный приём. Туннель работает как прежде. Если строка
мешает, задайте `AWG_NO_GRO=1`.
