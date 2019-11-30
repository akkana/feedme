#!/usr/bin/env python3

from __future__ import print_function

import unittest
from unittest.mock import Mock, patch
import time
import shutil
import sys, os

import feedmeparser
import feedme

sys.path.insert(0, '..')


def mock_downloader(url, referrer=None, user_agent=None, verbose=False):
    print("Mock downloader", url, referrer, user_agent, verbose)
    if url == 'http://rss.slashdot.org/Slashdot/slashdot':
        with open('test/samples/slashdot.rss') as fp:
            return fp.read()


class FeedmeTests(unittest.TestCase):

    # executed prior to each test
    def setUp(self):
        pass

    # executed after each test
    def tearDown(self):
        shutil.rmtree('test/testfeeds')
        pass

    #
    # To test networking, it would be nice to patch the opener returned
    # by urllib.request.build_opener, but that requires building a
    # whole Response by hand.
    #
    @patch('feedmeparser.FeedmeURLDownloader.download_url',
           side_effect=mock_downloader)
    def test_file_exists(self, themock):
        print("Testing something or other")
        config = feedmeparser.read_config_file("test/config")
        msglog = feedme.MsgLog()

        feedme.get_feed('Slashdot', config, None, None, msglog)

        dirpath = os.path.join('test', 'testfeeds', time.strftime("%m-%d-%a"))
        self.assertTrue(os.path.exists(dirpath))

        filepath = os.path.join(dirpath, 'Slashdot', 'index.html')
        self.assertTrue(os.path.exists(dirpath))

        testfilepath = os.path.join('test', 'samples', 'slashdot-test.html')
        with open(testfilepath) as testfp:
            with open(filepath) as fetchedfp:
                testcontents = testfp.read()
                fetchedcontents = fetchedfp.read()
                self.assertEqual(testcontents, fetchedcontents)
