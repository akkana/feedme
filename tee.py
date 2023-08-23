#!/usr/bin/env python3

import sys


class tee():
    '''A file-like class that can optionally send output to a log file.
       Inspired by
http://www.redmountainsw.com/wordpress/archives/python-subclassing-file-types
       and with IRC help from Kirk McDonald.
    '''
    def __init__(self, _fd1, _fd2):
        self.fd1 = _fd1
        self.fd2 = _fd2

    def __del__(self):
        if self.fd1 != sys.stdout and self.fd1 != sys.stderr:
            self.fd1.close()
        if self.fd2 != sys.stdout and self.fd2 != sys.stderr:
            self.fd2.close()

    def write(self, text):
        self.fd1.write(text)
        # UnicodeEncodeError: 'ascii' codec can't encode character '\u2019' in position 4: ordinal not in range(128)
        # fd1 is stderr.
        # fd2 was opened with: outputlog = open(logfilename, "w", buffering=1)
        # But it only happens when invoked from the web server,
        # maybe because the web server's environment is C rather than UTF-8,
        # and seemingly only when initiated from a phone (not from wget).
        try:
            self.fd2.write(text)
        except UnicodeEncodeError:
            s = "caught a UnicodeEncodeError trying to write a " \
                + str(type(text)) + '\n'
            self.fd1.write(s)
            self.fd2.write(s)
            # This just raises another error, probably for the same reason:
            # utils.ptraceback()

    def flush(self):
        self.fd1.flush()
        self.fd2.flush()


