From yt-dlp-ejs 0.8.0 (https://github.com/yt-dlp/ejs), unchanged:
`yt_dlp_ejs/yt/solver/core.min.js` and `lib.min.js`.

youtube/js_challenges.py runs them with Node to decode the stream URLs of
the clients that need YouTube's player script (the signed-in TV client).
When YouTube changes its player and decoding fails, update both files from
the newest yt-dlp-ejs release.

core: Unlicense (LICENSE). lib bundles meriyah (ISC) and astring (MIT); their
notices are at the top of yt.solver.lib.min.js.
