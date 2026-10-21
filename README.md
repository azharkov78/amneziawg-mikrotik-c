> This project is not affiliated with or endorsed by MikroTik / SIA Mikrotikls

# awg-proxy -- AmneziaWG для MikroTik

[![C11](https://img.shields.io/badge/C-11-blue)](https://en.cppreference.com/w/c/11)
[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

[English version](README_en.md) | [Конфигуратор](https://timbrs.github.io/amneziawg-mikrotik-c/conf3.0.html) | [Релизы](https://github.com/timbrs/amneziawg-mikrotik-c/releases) | [FAQ](docs/faq.md)

## Коротко

- **Что это.** Контейнер для RouterOS (~300 КБ), который подключает обычный WireGuard-интерфейс MikroTik к серверу AmneziaWG. Шифрует сам роутер, а контейнер только меняет вид пакетов: заголовки, паддинг, мусорные пакеты.
- **Протокол.** AmneziaWG 1.0, 1.5, 2.0, 3.0 и 3.1. Версия определяется сама по параметрам из `.conf`. Подходит и бесплатный конфиг Cloudflare WARP.
- **RouterOS.** 7.4+ с пакетом `container`. На 7.21+ нужен образ `awg-proxy-{arch}.tar.gz` (OCI), на 7.20 и ниже — `awg-proxy-{arch}-7.20-Docker.tar.gz`. На 7.22+ можно ставить прямо из реестра `ghcr.io/timbrs/awg-proxy:latest` и обновлять одной командой.
- **Железо.** arm64, arm (ARMv7), armv5 (hEX refresh, hEX S 2025 и hAP ax S, отдельный образ) и x86_64. **MIPS не поддерживается** (hEX, hEX S, hAP ac lite, hAP lite и т. п.): для него у RouterOS нет пакета container. Нужно 16 МБ свободной RAM.
- **Как поставить.** [Конфигуратор](https://timbrs.github.io/amneziawg-mikrotik-c/conf3.0.html): вставляете `.conf` из AmneziaVPN, получаете готовые команды для терминала MikroTik. Работает в браузере офлайн, ключи никуда не отправляются.
- **Режимы.** Клиент к серверу AmneziaWG (обычный), два MikroTik между собой (site-to-site), хаб на много клиентов (server).
- **Это актуальный репозиторий.** Старая Go-версия [timbrs/amneziawg-mikrotik](https://github.com/timbrs/amneziawg-mikrotik) больше не развивается.

Последняя сверка документации: 22 октября 2026, awg-proxy 1.4.3. Проверено на RouterOS 7.24.2 (hAP ax², arm64) и hAP ac² (arm) с серверами AmneziaWG 3.1 (amneziawg-go и модуль ядра).

## Пойдёт ли на моём роутере

Точный ответ даёт `/system/resource/print`, поле `architecture-name`: `arm64`, `arm` и `x86_64` подходят, `mipsbe`, `mmips`, `smips`, `tile` и `ppc` — нет. Конфигуратор сам выбирает образ по архитектуре, версии RouterOS и модели платы.

| Модели | Архитектура | Образ для RouterOS 7.21+ | Заметки |
|--------|-------------|--------------------------|---------|
| hAP ax², hAP ax³, RB5009, CCR2004, CCR2116, CCR2216 | arm64 | `awg-proxy-arm64.tar.gz` или `ghcr.io/timbrs/awg-proxy:latest` | Самый быстрый вариант. На 4 ядрах доступны пины потоков и `AWG_RT` |
| hAP ac², hAP ac³, RB4011, RB3011 | arm | `awg-proxy-arm.tar.gz` или `ghcr.io/timbrs/awg-proxy:latest` | У hAP ac² 16 МБ flash: ставьте на USB-накопитель в ext4, см. [неполадки](docs/troubleshooting.md#child-spawn-failed--could-not-load-next-layer) |
| hEX refresh (E50UG), hEX S 2025 (E60iUGS), hAP ax S (E62iUGS) | arm, но процессор понимает только ARMv5 | `awg-proxy-armv5.tar.gz` или `ghcr.io/timbrs/awg-proxy:latest-armv5` | Обычный arm-образ падает с `signal 4 (Illegal instruction)` |
| x86, CHR | x86_64 | `awg-proxy-amd64.tar.gz` или `ghcr.io/timbrs/awg-proxy:latest` | |
| hEX (RB750Gr3), hEX S (RB760iGS), hAP ac lite, hAP ac, hAP lite, RB2011, mAP | MIPS | — | **Не поддерживается**: у RouterOS нет пакета container для MIPS |
| CCR1009, CCR1016, CCR1036, CCR1072 | tile | — | **Не поддерживается** |

Для RouterOS 7.20 и ниже — те же имена с суффиксом `-7.20-Docker`, например `awg-proxy-arm-7.20-Docker.tar.gz`.

## Содержание

- [Как это работает](#как-это-работает)
- [Быстрый старт (конфигуратор)](#быстрый-старт-конфигуратор)
- [Установка без доступа к GitHub](#установка-без-доступа-к-github)
- [Требования](#требования)
- [Ручная установка](#ручная-установка)
- [Получение параметров AWG](#получение-параметров-awg)
- [Серверный режим (1:N) — подробная настройка](#серверный-режим-1n--подробная-настройка)
- [Дополнительные настройки](#дополнительные-настройки)
- [Обновление](#обновление)
- [Удаление](#удаление)
- [Устранение неполадок](#устранение-неполадок)
- [Сборка из исходников](#сборка-из-исходников)
- [Лицензия](#лицензия)

Подробная документация — отдельными файлами в [`docs/`](docs/):

| Файл | О чём |
|------|-------|
| [faq.md](docs/faq.md) | Частые вопросы своими словами: пойдёт ли на моём роутере, какая будет скорость, чем отличается от других контейнеров |
| [install.md](docs/install.md) | Где взять `.conf`, WARP, ручная установка по шагам, установка без доступа к GitHub |
| [update.md](docs/update.md) | Обновление из реестра, самолечение, переход со старой установки из файла |
| [env.md](docs/env.md) | Все переменные окружения контейнера |
| [protocol.md](docs/protocol.md) | AmneziaWG 3.0 и 3.1 на проводе, цепочка отката, IPv6 |
| [routing.md](docs/routing.md) | Маршруты через туннель, DNS, MTU и PPPoE, address-list |
| [server-mode.md](docs/server-mode.md) | Хаб 1:N на VPS или MikroTik |
| [troubleshooting.md](docs/troubleshooting.md) | Тексты ошибок и что с ними делать |

Краткая выжимка со ссылками для ИИ-ассистентов — [llms.txt](https://timbrs.github.io/amneziawg-mikrotik-c/llms.txt). Тем, кто правит код, — [AGENTS.md](AGENTS.md).

## Как это работает

### Стандартный режим (normal, по умолчанию)

```
MikroTik WG-клиент ──UDP──> [awg-proxy] ──UDP──> сервер AmneziaWG
   (шифрование)          (преобразование)          (обфускация)
```

Прокси заменяет заголовки пакетов, добавляет паддинг и мусорные пакеты так, чтобы сервер AmneziaWG принял трафик. Ключи и данные не затрагиваются.

### Режим reverse (mikrotik-to-mikrotik)

```
MikroTik1 WG ↔ [proxy1 normal] ──AWG──> [proxy2 reverse] ↔ MikroTik2 WG
```

Принимает AWG-трафик от normal-прокси, преобразует обратно в стандартный WireGuard и пересылает локальному WG-серверу. Позволяет соединить два MikroTik через AWG без поднятия отдельного AWG-сервера.
Это соединение вида точка-точка, не поддерживается мультисоединения.

### Режим awg-server (1:N)

```
proxy1a (normal) ──AWG──┐
proxy1b (normal) ──AWG──┤──> [reverse-hub] ──WG──> WG-сервер
proxy1c (normal) ──AWG──┘
```

Множественный обратный прокси: принимает AWG-подключения от нескольких normal-прокси и маршрутизирует ответы от WG-сервера к правильному клиенту через встроенную таблицу сессий. Для каждого пира используется ~16 байт в hash-таблице.

Подробная настройка описана в разделе [Серверный режим (1:N) — подробная настройка](#серверный-режим-1n--подробная-настройка).

Версия протокола (AmneziaWG 1.0–3.1) определяется автоматически по переменным окружения.

## Быстрый старт (конфигуратор)

0. Подготовьте роутер:
   - Установите пакет **container** с [mikrotik.com](https://mikrotik.com/download) (System → Packages), загрузите на роутер и перезагрузите
   - Включите device-mode:
     ```routeros
     /system/device-mode/update container=yes fetch=yes bandwidth-test=yes scheduler=yes
     ```
     Роутер попросит подтверждение (кнопка Reset/Mode или перезагрузка)
1. Экспортируйте `.conf`-файл из AmneziaVPN (см. [Получение параметров AWG](#получение-параметров-awg))
2. Откройте [конфигуратор](https://timbrs.github.io/amneziawg-mikrotik-c/conf3.0.html)
3. Вставьте содержимое `.conf`-файла
4. Скопируйте сгенерированные команды и выполните их в терминале MikroTik

Готово. Конфигуратор работает оффлайн, данные не отправляются на сервер.

<video src="https://github.com/user-attachments/assets/f0100789-0a23-42f8-a67f-085e5f8d13a3" controls width="100%"></video>

![Замеры скорости на MikroTik AX3](https://github.com/user-attachments/assets/9fb34444-681b-4f34-8306-8f202f1b121d)

*Замеры скорости на устройстве MikroTik AX3*

## Установка без доступа к GitHub

Включите в конфигураторе **офлайн-режим**: шаг 0 спросит у роутера архитектуру и версию RouterOS и напечатает, какие файлы скачать и куда положить. Подробно — [docs/install.md](docs/install.md#установка-без-доступа-к-github).

## Требования

- Сервер AmneziaWG с известными параметрами обфускации
- Файл конфигурации `.conf`, экспортированный из AmneziaVPN, или конфиг Cloudflare WARP
- MikroTik RouterOS 7.4+ с пакетом **container**
  - **RouterOS 7.21+**: стандартные образы `awg-proxy-{arch}.tar.gz` (OCI-формат)
  - **RouterOS 7.20 и ниже**: образы `awg-proxy-{arch}-7.20-Docker.tar.gz` (Docker-формат)
  - Конфигуратор определяет версию автоматически
- Архитектура: ARM64, ARM (v7), ARM (v5: hEX refresh / hEX S 2025 / hAP ax S) или x86_64, см. [таблицу моделей](#пойдёт-ли-на-моём-роутере) ([документация MikroTik](https://help.mikrotik.com/docs/spaces/ROS/pages/84901929/Container))
- Минимум 256 КБ свободного места на диске (или USB-накопитель)
- Минимум 16 МБ свободной оперативной памяти (RAM)

## Ручная установка

Без конфигуратора, командами RouterOS: пакет container, образ (файлом или из реестра `ghcr.io/timbrs/awg-proxy`), veth, WireGuard-интерфейс, переменные окружения, контейнер — [docs/install.md](docs/install.md#ручная-установка).

## Получение параметров AWG

1. Откройте приложение **AmneziaVPN**
2. Выберите нужное подключение
3. Нажмите **Поделиться** (Share)
4. Выберите: **Протокол**: AmneziaWG, **Формат**: AmneziaWG Format
5. Сохраните `.conf`-файл

Нет своего сервера AmneziaWG — в конфигураторе есть режим **«Нет AmneziaVPN — Cloudflare WARP»** с бесплатным конфигом WARP. Что делать, если в конфиге не хватает параметров, и как устроен режим WARP — [docs/install.md](docs/install.md#получение-параметров-awg).

## Серверный режим (1:N) — подробная настройка

Один awg-proxy на VPS или MikroTik обслуживает много клиентов: WireGuard-сервер плюс awg-proxy в режиме `server` (Docker Compose или systemd), добавление клиентов, прямые клиенты AmneziaWG, [маршрутизация между клиентами](docs/server-mode.md#маршрутизация-между-клиентами) — [docs/server-mode.md](docs/server-mode.md).

## Дополнительные настройки

- [Переменные окружения](docs/env.md) — полная таблица и подробное описание каждой
- [AmneziaWG 3.0, 3.1 и IPv6](docs/protocol.md) — что меняется на проводе, почему S1–S4 ≥ 12, цепочка отката, Happy Eyeballs
- [Маршрутизация, DNS и MTU](docs/routing.md) — маршруты через туннель, PPPoE и MTU, пределы DNS-маршрутизации, address-list

## Обновление

На RouterOS 7.22+ контейнер, поставленный из реестра, обновляется одной командой `/system/script/run awg-proxy-1-update` (скрипт кладёт конфигуратор; он же обновляет ночью в 04:30 и поднимает упавший контейнер). Вручную — `/container/repull`. Подробно — [docs/update.md](docs/update.md).

## Удаление

Если установка была через конфигуратор:

```routeros
/system/script/run awg-proxy-uninstall
```

Скрипт удалит контейнер, WireGuard-интерфейс, правила NAT, маршруты, переменные окружения, скрипт обновления с его scheduler'ом, восстановит DNS и удалит себя.

## Устранение неполадок

Ошибки с готовыми решениями — в [docs/troubleshooting.md](docs/troubleshooting.md):

- [`execvpe /awg-proxy: No such file or directory`](docs/troubleshooting.md#execvpe-awg-proxy-no-such-file-or-directory) — образ скачался не полностью
- [`Site-to-site: handshake did not complete`](docs/troubleshooting.md#site-to-site-handshake-did-not-complete)
- [`Storage device not found`](docs/troubleshooting.md#storage-device-not-found) — диск не отформатирован в ext4
- [`Insufficient disk space`](docs/troubleshooting.md#insufficient-disk-space)
- [`no space to extract layer`](docs/troubleshooting.md#no-space-to-extract-layer) — нет места под распаковку образа, есть обход через RAM
- [`start failed: no default dns servers`](docs/troubleshooting.md#start-failed-no-default-dns-servers) — RouterOS 7.24, у контейнера не задан DNS
- [`not allowed by device-mode`](docs/troubleshooting.md#not-allowed-by-device-mode)
- [`child spawn failed` / `could not load next layer`](docs/troubleshooting.md#child-spawn-failed--could-not-load-next-layer) — роутеры с 16 МБ flash
- [`exited with signal 4 (Illegal instruction)`](docs/troubleshooting.md#exited-with-signal-4-illegal-instruction) — hEX refresh / hEX S 2025 / hAP ax S, нужен образ armv5
- [Список RU-адресов не загружается после перезагрузки](docs/troubleshooting.md#список-ru-адресов-не-загружается-после-перезагрузки)
- [Handshake не проходит после восстановления из бэкапа](docs/troubleshooting.md#handshake-не-проходит-после-восстановления-из-бэкапа)

Нет рукопожатия, нет трафика после рукопожатия, контейнер перезапускается — в начале того же файла. Вопросы своими словами — в [FAQ](docs/faq.md).

## Сборка из исходников

Требуется C компилятор (gcc/musl-gcc), Docker (для контейнерных образов) и make.

```bash
# Тесты
make test

# Локальная сборка бинарника
make build

# Docker-образы (OCI, для RouterOS 7.21+)
make docker-arm64    # ARM64
make docker-arm      # ARM v7
make docker-armv5    # ARM v5
make docker-amd64    # x86_64
make docker-all      # Все архитектуры

# Docker-образы (классический формат, для RouterOS 7.20 и ниже)
make docker-arm64-7.20-docker
make docker-arm-7.20-docker
make docker-armv5-7.20-docker
make docker-amd64-7.20-docker
make docker-all-7.20-docker
```

Артефакты создаются в директории `builds/`. Устройство кода, инварианты и как гонять тесты — в [AGENTS.md](AGENTS.md).

У конфигуратора есть отдельный набор проверок на jsdom (только для разработки -- у самого прокси зависимостей нет):

```bash
npm install jsdom
node tests/conf3.0-ipv6.test.js
node tests/conf3.0-dns.test.js
node tests/conf3.0-ports.test.js
node tests/conf3.1.test.js
```

## Лицензия

MIT -- см. [LICENSE](LICENSE).
