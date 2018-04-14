#!/usr/bin/env python3

# Copyright (C) 2018 by Akkana Peck.
# Share and enjoy under the GPL v2 or later.

"""View RSS feeds pulled by FeedMe."""

from PyQt5.QtWidgets import QApplication, QMainWindow, QToolBar, QTabWidget, \
    QAction, QStatusBar, QProgressBar, QShortcut

import sys, os
import posixpath
import shutil

import quickbrowse

class DummyURLbar:
    def setText(self, s):
        pass

class FeedViewerWindow(quickbrowse.BrowserWindow):
    def __init__(self, *args, **kwargs):
        # Strip out arguments we handle that are different from QMainWindow:
        if 'feed_dir' in kwargs:
            self.feed_dir = kwargs['feed_dir']
            del kwargs['width']
        else:
            self.feed_dir = os.path.expanduser("~/feeds")

        super(FeedViewerWindow, self).__init__(*args, **kwargs)

        self.cur_feed = None
        self.cur_url = None
        self.last_url = None

        self.urlbar = DummyURLbar()

        # FeedViewer should remember where it was, but for now,
        # always start on the feed page.
        # We need to load something here, so quickbrowse will call new_tab
        # and give us self.webviews[0] so we can set up events.
        self.feed_page()

        self.webviews[0].urlChanged.connect(self.url_changed)
        self.webviews[0].loadFinished.connect(self.load_finished)

        # To get scroll position we need a QWebEnginePage.
        webpage = self.webviews[0].page()
        webpage.scrollPositionChanged.connect(self.scroll_position_changed)

    def init_chrome(self):
        self.setWindowTitle("FeedViewer")

        # Set up the minimal FeedViewer chrome
        toolbar = QToolBar("Toolbar")
        self.addToolBar(toolbar)

        btn_act = QAction("<<", self)
        # for an icon: QAction(QIcon("bug.png"), "Your button", self)
        btn_act.setStatusTip("Go back")
        btn_act.triggered.connect(self.go_back)
        toolbar.addAction(btn_act)

        btn_act = QAction("Feeds", self)
        btn_act.setStatusTip("Go forward")
        btn_act.triggered.connect(self.feed_page)
        toolbar.addAction(btn_act)

        btn_act = QAction("Del", self)
        btn_act.setStatusTip("Go forward")
        btn_act.triggered.connect(self.delete_feed)
        toolbar.addAction(btn_act)

        btn_act = QAction("ToC", self)
        btn_act.setStatusTip("Go forward")
        btn_act.triggered.connect(self.table_of_contents)
        toolbar.addAction(btn_act)

        btn_act = QAction(">>", self)
        btn_act.setStatusTip("Go forward")
        btn_act.triggered.connect(self.go_forward)
        toolbar.addAction(btn_act)

        self.webviews = []

        self.tabwidget = QTabWidget()
        self.tabwidget.setTabBarAutoHide(True)
        self.setCentralWidget(self.tabwidget)
        self.tabwidget.tabBar().installEventFilter(self)
        self.prev_middle = -1
        self.active_tab = 0

        self.setStatusBar(QStatusBar(self))
        self.progress = QProgressBar()
        self.statusBar().addPermanentWidget(self.progress)

        # Key bindings.
        # For keys like function keys, use QtGui.QKeySequence("F12")
        QShortcut("Ctrl+Q", self, activated=self.close)
        QShortcut("Ctrl+L", self, activated=self.select_urlbar)
        QShortcut("Ctrl+T", self, activated=self.new_tab)
        QShortcut("Ctrl+R", self, activated=self.reload)

        QShortcut("Alt+Left", self, activated=self.go_back)
        QShortcut("Alt+Right", self, activated=self.go_forward)

    def go_back(self):
        self.webviews[self.active_tab].back()

    def go_forward(self):
        self.webviews[self.active_tab].forward()

    def feed_page(self):
        '''Load the top-level page showing feeds available.'''

        self.cur_feed = None
        self.cur_url = None

        html = ''

        # Loop over directories inside the feed dir, which should mostly be days
        for d in os.listdir(self.feed_dir):
            full_d = os.path.join(self.feed_dir, d)
            if not os.path.isdir(full_d):
                continue
            day_feeds_html = ''

            # Loop over directories in the day dir, which should be feeds:
            dayfeeds = os.listdir(full_d)
            dayfeeds.sort()
            for f in dayfeeds:
                full_f = os.path.join(full_d, f)
                if not os.path.isdir(full_f):
                    continue
                index = os.path.join(full_f, "index.html")
                if not os.path.exists(index):
                    continue
                day_feeds_html += '<p><a href="file://%s">%s</a>' % (index, f)

            # Did we get any valid feeds?
            if day_feeds_html:
                html +=  '<p>\n%s:\n%s' % (d, day_feeds_html)

        if not html:
            html = "<p>\nNo feeds found\n"

        # QtWebEngine ignores <link rel="stylesheet href=[relative]"
        # in setHtml(), so use an absolute path for the CSS.
        html = '''<html>
<head>
<title>Feeds</title>
<link rel="stylesheet" type="text/css" title="Feeds" href="file://%s/feeds.css">
</head>
<body>
%s
</body>
</html>
''' % (self.feed_dir, html)

        # print("html:", html)
        # print("Feed dir:", self.feed_dir)
        # print("base:", 'file://' + self.feed_dir)
        self.load_html(html, 'file://' + self.feed_dir)

    def delete_feed(self):
        thisfeeddir = posixpath.join(self.feed_dir, self.cur_feed)
        print("deleting", thisfeeddir)
        shutil.rmtree(thisfeeddir)
        self.feed_page()

    def table_of_contents(self):
        self.load_url('file://' + posixpath.join(self.feed_dir,
                                                 self.cur_feed,
                                                 "index.html"))

    def reload(self):
        self.webviews[self.active_tab].reload()

    def whichfeed(self, url):
        '''Figure out which feed we're on from the URL'''
        if url.scheme() != 'file':
            return None

        path = url.path()
        print("whichfeed", path)
        if not path.startswith(self.feed_dir):
            return None
        path = path[len(self.feed_dir) + 1:]
        if not path:
            return None

        splitpath = path.split('/')
        if len(splitpath) < 2:
            print("Yikes, splitpath has too few components:", splitpath)
            return splitpath[0]
        return splitpath[0] + '/' + splitpath[1]

    #
    # Slots, in addition to what the parent BrowserWindow class registers:
    #

    def url_changed(self, url):
        # print("FeedmeView URL changed", url)
        self.last_url = url

    def load_finished(self, ok):
        # print("FeedmeView load finished", ok)
        if ok and self.last_url:
            self.cur_url = self.last_url
            self.cur_feed = self.whichfeed(self.cur_url)
        self.last_url = None

    def scroll_position_changed(self, position):
        print("Scroll position changed:", position)
        print("Page says", self.webviews[0].page().scrollPosition())
        # Happily, these are the same. They seem to be in pixels.
        print("Contents size", self.webviews[0].page().contentsSize())

        # If this doesn't work out, there are also apparently
        # ways of getting scroll pos from JS. See _update_pos() in
        # qutebrowser/qutebrowser/browser/webengine/webenginetab.py

if __name__ == '__main__':
    # # Return control to the shell before creating the window:
    # rc = os.fork()
    # if rc:
    #     sys.exit(0)

    app = QApplication(sys.argv)

    win = FeedViewerWindow(width=500, height=700)

    win.show()

    app.exec()
