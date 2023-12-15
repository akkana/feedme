#!/usr/bin/env python3

import os
import pathlib
import requests
from http.cookiejar import MozillaCookieJar

#######################################
# https://stackoverflow.com/a/62112043

def cookies_from_cookies_dot_txt():
    cookiesFile = str(pathlib.Path(__file__).parent.absolute() / "cookies.txt")  # Places "cookies.txt" next to the script file.
    cj = MozillaCookieJar(cookiesFile)
    if os.path.exists(cookiesFile):  # Only attempt to load if the cookie file exists.
        cj.load(ignore_discard=True, ignore_expires=True)  # Loads session cookies too (expirydate=0).

    s = requests.Session()
    s.headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/81.0.4044.138 Safari/537.36",
        "Accept-Language": "en-US,en"
    }
    s.cookies = cj  # Tell Requests session to use the cookiejar.

    # DO STUFF HERE WHICH REQUIRES THE PERSISTENT COOKIES...
    s.get("https://www.somewebsite.com/")

    cj.save(ignore_discard=True, ignore_expires=True)  # Saves session cookies too (expirydate=0).


#######################################
# https://blog.mithis.net/archives/python/90-firefox3-cookies-in-python

def sqlite2cookie_mithis(filename):
    from cStringIO import StringIO
    from pysqlite2 import dbapi2 as sqlite

    con = sqlite.connect(filename)

    cur = con.cursor()
    cur.execute("select host, path, isSecure, expiry, name, value from moz_cookies")

    ftstr = ["FALSE","TRUE"]

    s = StringIO()
    s.write("""\
# Netscape HTTP Cookie File
# http://www.netscape.com/newsref/std/cookie_spec.html
# This is a generated file!  Do not edit.
""")
    for item in cur.fetchall():
        s.write("%s\t%s\t%s\t%s\t%s\t%s\t%s\n" % (
            item[0], ftstr[item[0].startswith('.')], item[1],
            ftstr[item[2]], item[3], item[4], item[5]))

    s.seek(0)

    cookie_jar = cookielib.MozillaCookieJar()
    cookie_jar._really_load(s, '', True, True)
    return cookie_jar


#######################################
# https://stackoverflow.com/a/33078599

COOKIE_FILE = '/home/akkana/.mozilla/firefox/a6sezjo1.selenium/cookies.sqlite'

def get_cookie_jar(filename):
    """
    Protocol implementation for handling gsocmentors.com transactions
    Author: Noah Fontes nfontes AT cynigram DOT com
    License: MIT
    Original: http://blog.mithis.net/archives/python/90-firefox3-cookies-in-python

    Ported to Python 3 by Dotan Cohen
    """

    from io import StringIO
    import http.cookiejar
    import sqlite3

    con = sqlite3.connect(filename)
    cur = con.cursor()
    cur.execute("SELECT host, path, isSecure, expiry, name, value FROM moz_cookies")

    ftstr = ["FALSE","TRUE"]

    s = StringIO()
    s.write("""\
# Netscape HTTP Cookie File
# http://www.netscape.com/newsref/std/cookie_spec.html
# This is a generated file!  Do not edit.
""")

    for item in cur.fetchall():
        s.write("%s\t%s\t%s\t%s\t%s\t%s\t%s\n" % (
            item[0], ftstr[item[0].startswith('.')], item[1],
            ftstr[item[2]], item[3], item[4], item[5]))

    s.seek(0)
    cookie_jar = http.cookiejar.MozillaCookieJar()
    cookie_jar._really_load(s, '', True, True)

    return cookie_jar


cj = get_cookie_jar(COOKIE_FILE)
# print(cj)

url = 'https://www.nytimes.com/2021/11/06/us/dark-sky-parks-us.html'
response = requests.get(url, cookies=cj)
html = response.text
print("Got", len(html), "bytes")
outfile = '/tmp/nytfoo.html'
with open(outfile, 'w') as fp:
    print(html, file=fp)
    print("Saved", outfile)

