# Word lists used by the PII scan

`piibench.pii_scan` uses two word lists to tell ordinary English words and common given names apart from other
tokens. They are bundled so that the scan gives the same result on every platform.

* `web2.txt.gz`: Webster's Second International Dictionary word list (234,936 words), as distributed in BSD
  `/usr/share/dict/web2`. The 1934 copyright has lapsed.
* `propernames.txt`: the BSD `/usr/share/dict/propernames` list of given names.

Both files are taken unmodified from the FreeBSD / macOS `/usr/share/dict` directory.
