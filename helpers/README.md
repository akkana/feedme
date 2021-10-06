# FeedMe Helpers

Some sites make fetching feeds too complicated to handle through
the normal FeedMe process.

For instance, the New York Times doesn't allow fetching stories
through their API, and even if you're a subscriber, they don't allow
any simple method for providing login credentials: you have to have a
full browser environment with JavaScript and a logged-in browser
profile.

To make things more complicated, you may need to fetch pages on a
machine other than the one that's normally assembling your feeds.
For instance, you may not want to run a desktop browser (and all
the X machinery it needs) on your web server.

Enter feedme helpers. A ```page_helper``` can fetch single pages
one at a time. The HTML will be passed back to feedme, which can
then do its normal operations and store the result in the feeds directory.

Eventually it may also be possible to have a ```feed_helper``` which
gathers a whole feed on its own with no help from feedme. For
instance, a site that has no RSS would need a feed_helper,
since feedme can only get the list of stories from RSS.
With a feed_helper you would have to manage last-seen dates
on your own.


## Writing a Page Helper:

Let's say you want to feed the New York Times. You're a subscriber,
and you have a Firefox profile with the appropriate cookies.

In your site file, add a line like

```
page_helper = nyt_selenium
```

This tells feedme to import a file nyt_selenium.py, which must either
be in the *helpers/* directory wherever feedme is installed, or
somewhere in your PYTHONPATH.

nyt_selenium.py must define the following functions:

```
initialize()
    # can be a no-op

fetch_article(url)
    # Returns the desired html as a string (not bytes).
    # This can be further processed by feedme
    # if your site file has directives like page_start,
    # skip_pat etc.
```


## Minimal Helper:

In some cases you may need a minimal helper that doesn't fetch
anything at all, but simply copies files or accepts files that
have already been copied into the day's feed directory.
For instance, you'd need this if you need to run selenium on a machine
other than the main feedme server.

TODO write minimal helper
