FeedMe: a lightweight RSS/feed fetcher.

FeedMe is a program to fetch stories from RSS (or Atom) feeds
to a local directory
so they can be read offline, especially on a small device like a phone,
PDA or ebook reader.
It's sort of an RSS version of Sitescooper or AvantGo.

You specify which feeds you want to read, and run feedme once a day.
Feedme fetches the stories (and, optionally, images), cleans up the HTML,
and optionally converts to a format other than HTML if your reading device
prefers some other format (e.g. epub).

FeedMe is written in Python and uses the feedparser module,
as well as various other dependencies like BeautifulSoup, lxml.html,
and cookiejar.

[The documentation for feedme is on my website](http://shallowsky.com/software/feedme/),
or in in docs/feedme.html in this repository.
