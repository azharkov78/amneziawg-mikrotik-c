"""Real AWG engine, two isolated normal/reverse routes; WG-shaped data, no crypto."""
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import socket
import struct
import subprocess
import tempfile
import threading
import time

from test_supervisor import COMMON, port

binary = os.environ['AWG_REAL_PROXY']
supervisor = os.environ['AWG_SUPERVISOR']


def main():
    children, sockets, threads = [], [], []
    stop = threading.Event()
    logs = []
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        try:
            normal, reverse = root / 'normal', root / 'reverse'
            normal.mkdir()
            reverse.mkdir()
            # Keep scheduling/performance tuning off in this functional test.
            common = COMMON + 'AWG_SPIN=0\nAWG_NO_GRO=1\nAWG_NO_GSO=1\nAWG_SOCKET_BUF=1048576\n'
            for directory in (normal, reverse):
                (directory / 'common.env').write_text(common)
            locals_ = []
            for i in range(2):
                echo = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                echo.bind(('127.0.0.1', 0))
                echo.settimeout(.2)
                sockets.append(echo)

                def serve(sock=echo, marker=bytes([i])):
                    while not stop.is_set():
                        try:
                            data, addr = sock.recvfrom(65535)
                            # Distinct destination identity, preserving the WG-shaped header.
                            sock.sendto(data[:-1] + marker, addr)
                        except socket.timeout:
                            continue
                        except OSError:
                            break

                thread = threading.Thread(target=serve)
                thread.start()
                threads.append(thread)
                front, listen = port(), port()
                locals_.append(listen)
                profile = ''.join(f'AWG_H{j}={1000 + i * 10 + j}\n' for j in range(1, 5))
                (normal / f'route{i}.env').write_text(
                    f'AWG_LISTEN=127.0.0.1:{listen}\nAWG_REMOTE=127.0.0.1:{front}\nAWG_MODE=normal\n' + profile)
                (reverse / f'route{i}.env').write_text(
                    f'AWG_LISTEN=127.0.0.1:{front}\nAWG_REMOTE=127.0.0.1:{echo.getsockname()[1]}\nAWG_MODE=reverse\n' + profile)
            for directory in (reverse, normal):
                log = open(root / (directory.name + '.log'), 'w+')
                logs.append(log)
                env = dict(os.environ, AWG_CONFIG_DIR=str(directory), AWG_PROXY_BIN=binary)
                children.append(subprocess.Popen([supervisor], env=env, stderr=log, stdout=log))
            time.sleep(.5)

            def exercise(i):
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as client:
                    client.settimeout(.3)
                    for sequence in range(100):
                        # Upstream AWG non-GRO receive slots are 1500 bytes;
                        # exercise ordinary WG datagrams within that bound.
                        size = (32, 128, 1400, 1480)[sequence % 4]
                        packet = struct.pack('<IIQ', 4, 100 + i, sequence) + os.urandom(size - 16)
                        expected = packet[:-1] + bytes([i])
                        for attempt in range(20 if sequence == 0 else 1):
                            client.sendto(packet, ('127.0.0.1', locals_[i]))
                            try:
                                reply = client.recv(65535)
                            except socket.timeout:
                                if attempt == (19 if sequence == 0 else 0): raise
                                continue
                            assert reply == expected, (i, sequence, len(reply), size)
                            break
                return 100

            with ThreadPoolExecutor(max_workers=2) as pool:
                counts = list(pool.map(exercise, range(2)))
            for child in children:
                assert child.poll() is None
                child.terminate()
            for child in children:
                child.wait(timeout=8)
                assert child.returncode == 0
            for log in logs:
                log.seek(0)
                text = log.read()
                assert '[route0] INFO:' in text and '[route1] INFO:' in text
            print(f'PASS: {sum(counts)} byte-exact round trips, 2 simultaneous routes, distinct profiles and targets')
            print('WG-shaped datagrams only; real WireGuard handshake and router throughput were not tested.')
        except Exception:
            for log in logs:
                log.seek(0)
                print(log.read())
            raise
        finally:
            for child in children:
                if child.poll() is None: child.terminate()
            for child in children:
                try: child.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait()
            stop.set()
            for thread in threads: thread.join(timeout=1)
            for sock in sockets: sock.close()
            for log in logs: log.close()


if __name__ == '__main__':
    main()
