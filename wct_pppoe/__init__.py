#!/usr/bin/python3
# -*- coding: utf-8; tab-width: 4; indent-tabs-mode: t -*-

import os
import sys
import time
import shutil
import subprocess
import multiprocessing


def get_plugin_list(self):
    return [
        "cn-bj-gwbn-4m",             # 中国北京长城宽带4M
    ]


def get_plugin(self, name):
    if name == "cn-bj-gwbn-4m":
        return _PluginObject(4 * 1024 * 1024 / 8)
    else:
        assert False


class _PluginObject:

    def __init__(self, bandwidth):
        self.bandwidth = bandwidth              # byte/s

    def init2(self, cfg, tmpDir, ownResolvConf):
        self.cfg = cfg
        self.tmpDir = tmpDir
        self.ownResolvConf = ownResolvConf

    def start(self):
        _Util.ifUp(self.cfg["interface"])

        if "username" in self.cfg:
            username = self.cfg["username"]
        else:
            username = ""
        if "password" in self.cfg:
            password = self.cfg["password"]
        else:
            password = ""

        self.proc = multiprocessing.Process(target=self._subprocPppoe,
                                            args=("", self.cfg["interface"], username, password, ))
        self.proc.start()

        while not os.path.exists(os.path.join(self.tmpDir, "etc-ppp", "resolv.conf")):
            time.sleep(1.0)

    def stop(self):
        if self.proc is not None:
            self.proc.terminate()
            self.proc.join()
            self.proc = None

    def getOutInterface(self):
        return "wrt-ppp-wan"

    def _subprocPppoe(self, optionTemplate, interface, username, password):
        tmpEtcPppDir = os.path.join(self.tmpDir, "etc-ppp")
        tmpPapSecretsFile = os.path.join(tmpEtcPppDir, "pap-secrets")
        tmpIpUpScript = os.path.join(tmpEtcPppDir, "ip-up")
        tmpIpDownScript = os.path.join(tmpEtcPppDir, "ip-down")
        tmpPeerFile = os.path.join(tmpEtcPppDir, "peers", "wan")
        proc = None

        try:
            os.mkdir(tmpEtcPppDir)

            with open(tmpPapSecretsFile, "w") as f:
                buf = ""
                buf += "%s wan \"%s\" *\n" % (username, password)
                f.write(buf)
            os.chmod(0o600, tmpPapSecretsFile)

            with open(tmpIpUpScript, "w") as f:
                buf = ""
                buf += "#!/bin/sh\n"
                buf += "\n"
                buf += "echo \"# Generated by wrtd\" > %s\n" % (self.ownResolvConf)
                buf += "[ -n \"$DNS1\" ] && echo \"nameserver $DNS1\" >> %s\n" % (self.ownResolvConf)
                buf += "[ -n \"$DNS2\" ] && echo \"nameserver $DNS2\" >> %s\n" % (self.ownResolvConf)
                f.write(buf)
            os.chmod(0o755, tmpPapSecretsFile)

            os.chmod(0o755, tmpIpUpScript)

            with open(tmpIpDownScript, "w") as f:
                buf = ""
                buf += "#!/bin/sh\n"
                buf += "\n"
                buf += "echo \"\" > %s\n" % (self.ownResolvConf)
            os.chmod(0o755, tmpIpDownScript)

            os.mkdir(os.path.dirname(tmpPeerFile))
            with open(tmpPeerFile, "w") as f:
                buf = optionTemplate.replace("$USERNAME", username)
                buf += "\n"
                buf += "pty \"pppoe -I %s\"\n" % (interface)
                buf += "lock\n"
                buf += "noauth\n"
                buf += "ifname wrt-ppp-wan\n"
                buf += "persist\n"
                buf += "holdoff 10\n"
                buf += "defaultroute\n"
                buf += "usepeerdns\n"
                buf += "remotename wan\n"
                buf += "user %s\n" % (username)
                f.write(buf)

            with _UtilNewMountNamespace():
                # pppd read config files from the fixed location /etc/ppp
                # this behavior is bad so we use mount namespace to workaround it
                subprocess.check_output(["/bin/mount", "--bind", tmpEtcPppDir, "/etc/ppp"])
                cmd = "/usr/sbin/pppd call wan nodetach"
                proc = subprocess.Popen(cmd, shell=True, universal_newlines=True)
                proc.wait()
        finally:
            if os.path.exists(tmpEtcPppDir):
                shutil.rmtree(tmpEtcPppDir)
            if proc is not None:
                sys.exit(proc.returncode)


class _Util:

    @staticmethod
    def ifUp(ifname):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            ifreq = struct.pack("16sh", ifname, 0)
            ret = fcntl.ioctl(s.fileno(), 0x8913, ifreq)
            flags = struct.unpack("16sh", ret)[1]                   # SIOCGIFFLAGS
            flags |= 0x1
            ifreq = struct.pack("16sh", ifname, flags)
            fcntl.ioctl(s.fileno(), 0x8914, ifreq)                  # SIOCSIFFLAGS
        finally:
            s.close()


class _UtilNewMountNamespace:

    _CLONE_NEWNS = 0x00020000               # <linux/sched.h>
    _MS_REC = 16384                         # <sys/mount.h>
    _MS_PRIVATE = 1 << 18                   # <sys/mount.h>
    _libc = None
    _mount = None
    _setns = None
    _unshare = None

    def __init__(self):
        if self._libc is None:
            self._libc = ctypes.CDLL('libc.so.6', use_errno=True)
            self._mount = self._libc.mount
            self._mount.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p, ctypes.c_ulong, ctypes.c_char_p]
            self._mount.restype = ctypes.c_int
            self._setns = self._libc.setns
            self._unshare = self._libc.unshare

        self.parentfd = None

    def __enter__(self):
        self.parentfd = open("/proc/%d/ns/mnt" % (os.getpid()), 'r')

        # copied from unshare.c of util-linux
        try:
            if self._unshare(self._CLONE_NEWNS) != 0:
                e = ctypes.get_errno()
                raise OSError(e, errno.errorcode[e])

            srcdir = ctypes.c_char_p("none".encode("utf_8"))
            target = ctypes.c_char_p("/".encode("utf_8"))
            if self._mount(srcdir, target, None, (self._MS_REC | self._MS_PRIVATE), None) != 0:
                e = ctypes.get_errno()
                raise OSError(e, errno.errorcode[e])
        except:
            self.parentfd.close()
            self.parentfd = None
            raise

    def __exit__(self, *_):
        self._setns(self.parentfd.fileno(), 0)
        self.parentfd.close()
        self.parentfd = None
