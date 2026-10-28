#!/usr/bin/env python3
"""Static musl cross-build without ARM binfmt; requires Zig 0.14.1 and Docker."""
import argparse
from pathlib import Path
import subprocess

TARGETS = {
    'armv7': ('arm-linux-musleabihf', 'cortex_a7', 'linux/arm/v7'),
    'arm64': ('aarch64-linux-musl', 'baseline', 'linux/arm64'),
    'amd64': ('x86_64-linux-musl', 'baseline', 'linux/amd64'),
}
SOURCES = ['main', 'proxy', 'transform', 'blake2s', 'chacha20', 'cps', 'fastrand', 'base64', 'log']


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--zig', default='zig')
    p.add_argument('--arch', choices=TARGETS, required=True)
    p.add_argument('--version', default='supervisor-dev')
    p.add_argument('--output', default='builds/multiroute')
    p.add_argument('--image', action='store_true', help='build a scratch Docker image from the binaries')
    a = p.parse_args()
    root = Path(__file__).resolve().parent.parent
    out = Path(a.output).resolve() / a.arch
    out.mkdir(parents=True, exist_ok=True)
    target, cpu, platform = TARGETS[a.arch]
    flags = [a.zig, 'cc', '-target', target, '-mcpu=' + cpu, '-O2', '-Wall', '-Wextra',
             # Zig 0.14.1's musl CMSG_NXTHDR expands a signed/unsigned compare.
             '-Werror', '-Wno-error=sign-compare', '-std=c11', '-D_GNU_SOURCE', '-DVERSION="' + a.version + '"',
             '-ffunction-sections', '-fdata-sections', '-Wl,--gc-sections', '-static', '-s']
    for name, sources in [('awg-proxy', SOURCES), ('awg-supervisor', ['supervisor'])]:
        command = flags + ['-o', str(out / name)]
        if name == 'awg-proxy': command.append('-pthread')
        subprocess.run(command + [str(root / 'src' / (s + '.c')) for s in sources], check=True)
    for name in ('awg-proxy', 'awg-supervisor'):
        (out / name).chmod(0o755)
    if a.image:
        subprocess.run(['docker', 'build', '--platform', platform,
                        '-f', str(root / 'Dockerfile.multiroute-prebuilt'),
                        '-t', 'awg-proxy:' + a.version + '-' + a.arch, str(out)], check=True)
    print('Built:', a.arch, out)


if __name__ == '__main__':
    main()
