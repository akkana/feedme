#!/usr/bin/env python3

from __future__ import print_function

import unittest
from unittest.mock import Mock, patch
import time
from datetime import datetime
import tempfile
import subprocess
import time
import shutil
import filecmp
import sys, os

import pageparser
import feedme
import utils
import msglog

sys.path.insert(0, '..')


def mock_downloader(url, referrer=None, user_agent=None, verbose=False):
    print("Mock downloader", url, referrer, user_agent, verbose)
    if url == 'http://rss.slashdot.org/Slashdot/slashdot':
        with open('test/samples/slashdot.rss') as fp:
            return fp.read()


class TestCaseWithSave(unittest.TestCase):
    def assertLongStringEqual(self, expected, actual):
        if expected == actual:
            return

        # They're not equal. Save each one to a file.
        tmpdir = tempfile.mkdtemp()
        # Can't use tmpfile.TemporaryDirectory, it deletes the directory on exit
        # which means there's no chance of copying the new actual output
        # into expected, if that's desired.

        expname = os.path.join(tmpdir, "expected")
        with open(expname, "w") as fp:
            fp.write(expected)
        actname = os.path.join(tmpdir, "actual")
        with open(actname, "w") as fp:
            fp.write(actual)

        for diffprog in ("tkdiff", "meld"):
            try:
                subprocess.Popen([diffprog, expname, actname])
                break
            except FileNotFoundError:
                continue

        # Both of these options work, but in both cases the printed stack trace
        # points to this line in TestCaseWithSave.assertLongStringEqual.
        # I'd like to find a way to get it to ignore the line inside
        # assertLongStringEqual and just give the failed line from the
        # test function, just like other UnitTest assertions do.
        # raise AssertionError("Long strings not equal. Saved to %s and %s"
        #                      % (expname, actname))
        self.fail("Long strings not equal. Saved to %s and %s"
                  % (expname, actname))

    def read_two_files(self, expectfile, newfile):
        """Read the contents of two files into strings, returning the strings,
           which can be compared with self.assertLongStringEqual().
           newfile is the file just generated; expectfile will have XXXDAYXXX
           replace with today's day name (e.g. Fri).
        """
        with open(expectfile, encoding='utf-8') as expectfp:
            # The generated HTML file will have the weekday set to today.
            # The test file has a placeholder that needs to be replaced.
            today = datetime.now().strftime("%a")
            expectcontents = expectfp.read().replace('XXXDAYXXX', today)

        with open(newfile, encoding='utf-8') as newfp:
            newcontents = newfp.read()

        return expectcontents, newcontents


class FeedmeTests(TestCaseWithSave):

    # executed prior to each test
    def setUp(self):
        pass

    # executed after each test
    def tearDown(self):
        pass

    #
    # To test networking, it would be nice to patch the opener returned
    # by urllib.request.build_opener, but that requires building a
    # whole Response by hand.
    #
    @patch('pageparser.FeedmeURLDownloader.download_url',
           side_effect=mock_downloader)
    def test_file_exists(self, themock):
        config = utils.read_config_file("test/config")

        feedme.get_feed('Slashdot', None, None, msglog)

        dirpath = os.path.join('test', 'testfeeds', time.strftime("%m-%d-%a"))
        self.assertTrue(os.path.exists(dirpath))

        fetchedfilepath = os.path.join(dirpath, '01_Slashdot', 'index.html')
        print("========= checking for", fetchedfilepath)
        self.assertTrue(os.path.exists(fetchedfilepath))

        expectfilepath = os.path.join('test', 'samples', 'slashdot-test.html')
        expectcontents, fetchedcontents = self.read_two_files(expectfilepath,
                                                              fetchedfilepath)
        self.assertLongStringEqual(expectcontents, fetchedcontents)

        shutil.rmtree('test/testfeeds')

    def test_config_file_parsing(self):
        """Try to guard against bad config files killing feedme,
           like if someone omits an = sign.
        """

        configstr = """[Test Config]
url blah
page_start = <div class="menu-primary-container">
page_end = <div id="comment-form-nascar">
"""
        # Now how do we test it? The real-world error I saw came from
        # feedme.py line 1534, if feedurl == config.get(feedname, 'url')
        # while cleaning up old feeds after a feed's URL.
        # But that requires having a whole feedme session going,
        # and config.get happens from various places.
        # Best might be to override config.get so it doesn't raise exceptions.
        # Related discussion:
        # https://stackoverflow.com/questions/24832628/python-configparser-getting-and-setting-without-exceptions

    def test_wired(self):
        """This primarily tests skip_nodes"""
        TMPDIR = "test/tmp"
        try:
            os.mkdir(TMPDIR)
        except FileExistsError:
            pass

        CONFFILE = 'test/config/wired.conf'
        shutil.copyfile('siteconf/wired.conf', CONFFILE)
        utils.read_config_file(confdir='test/config')

        fmp = pageparser.FeedmeHTMLParser('Wired')
        fmp.fetch_url('file://test/samples/wired-orig.html', TMPDIR, '0.html')

        expectcontents, fetchedcontents = self.read_two_files(
            'test/samples/wired-simplified.html',
            os.path.join(TMPDIR, '0.html'))

        shutil.rmtree(TMPDIR)
        os.unlink(CONFFILE)

        self.assertLongStringEqual(expectcontents, fetchedcontents)
