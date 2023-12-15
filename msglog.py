#!/usr/bin/env python3

import sys


##################################################################
# MsgLog: Print messages and also batch them up to print at the end:
#
class MsgLog:
    def __init__(self):
        self.msgstr = ""
        self.errstr = ""

    def msg(self, s):
        self.msgstr += "\n" + s
        print("MESSAGE:", s, file=sys.stderr)

    def warn(self, s):
        self.msgstr += "\n" + s
        print("WARNING:", s, file=sys.stderr)

    def err(self, s):
        self.errstr += "\n" + s
        print("ERROR:", s, file=sys.stderr)

    def get_msgs(self):
        return self.msgstr

    def get_errs(self):
        return self.errstr


gMsglog = MsgLog()

def msg(s):
    gMsglog.msg(s)

def warn(s):
    gMsglog.warn(s)

def err(s):
    gMsglog.err(s)

def get_msgs():
    return gMsglog.get_msgs()

def get_errs():
    return gMsglog.get_errs()
