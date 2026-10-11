# Plan: terminal link inspector

A read-only terminal browser for checking suspicious links, with a local AI that explains its findings. Paste a link and see what the page is doing before opening it in a real browser. It also works as a calm, distraction-free reader for ordinary pages.

Solo build, local-first (Ollama), built with Textual. **Target prize: Best Use of Gemma 4**, an on-device Gemma doing real security work on a laptop CPU.

Working name: `linkinspector` (used for the cache folder; rename freely).

## How it works

```
URL ──► URL checks (before any request is sent)
          │ warn, then you confirm
          ▼
     Fetch the page
       • default: static fetch (httpx), no JavaScript
       • press r: headless Chromium (Playwright)
           – context.route(): .test pages, adblock filtering, request log
           – local policy proxy: checks every connection, including redirect steps
          ▼
     Snapshot (immutable, one per inspection)
       page ID, mode, capture time, requested/final URL, redirects,
       original response HTML, post-JavaScript DOM (Chromium mode only),
       links, forms, request log, findings, coverage notes
          ├─► security checks (on the HTML, before text extraction)
          ├─► readable Markdown → Textual reader view
          └─► Gemma on Ollama (background, optional)
```

- **Every navigation or mode switch creates a new snapshot.** Rendering, checks and chat reuse it without fetching again.
- **Switching to JavaScript mode is a new inspection.** It contacts the site again, and the UI says so.
- **Original HTML and the post-JavaScript DOM come from the same Chromium page load.** The original HTML is the navigation response body (`response.text()`); the DOM is `page.content()`. Never compare against the earlier static fetch, which could have been served different content.
- **Check that the page didn't change while it was being captured.**
  - Right after the page loads, set a marker in it (`window.__inspect_id = "<id>"`) and record main-frame navigations with `framenavigated`.
  - Before capturing the final page, check the marker is still there. If it's gone, the page navigated to a new document: try capturing once more.
  - If it changes again, mark the "JS-added form" comparison as unavailable.
  - Record a JavaScript-triggered navigation as a **JavaScript redirect** in the redirect chain. It's evidence in its own right.

## Features

### Core (the minimum to finish today)
- **A Textual reader:**
  - URL bar, back/forward over captured snapshots, and the redirect chain
  - links resolved against the page's `<base>`, each always showing its real destination in the status bar
  - the Markdown widget never opens links by itself (`open_links=False`); every click goes through your own handler and URL checks
- **URL checks, run before fetching:**
  - lookalikes of a small, explicit list of commonly phished brands, including UK targets such as HMRC, Royal Mail, Evri, Monzo and the big banks
  - IDNA/punycode and Unicode lookalike characters, with both forms of the hostname shown
  - misleading subdomains, using the Public Suffix List with private suffixes like `github.io` included
  - links that point at a bare IP address
- **Page checks on the HTML:**
  - password forms, and forms that send data to another site, noting when it looks like normal single sign-on
  - password fields on plain HTTP
  - link text that claims a different domain than the link actually goes to
  - each finding has a stable evidence ID and shows what was seen and why it matters, not just a verdict
  - "form added by JavaScript" is only claimed when the original HTML and the DOM come from the same load **and** the same document
- **Gemma explanations** using `gemma4:e2b-it-qat` on Ollama (details under *Local Gemma*)
- **Separate status labels:** code findings; AI pending / done / unavailable; filtering active / unavailable; coverage complete / partial. Say "no findings in inspected content", never "safe". Failures, empty extraction and a disabled model stay visible.

### Next
- **JavaScript mode in headless Chromium:**
  - `adblock` filtering with EasyList/EasyPrivacy in `context.route()`
  - every connection, including each redirect step, checked by the local policy proxy (see *Safety rules*)
  - a request panel that keeps three measurements apart: tracker references in the HTML, requests seen, and requests blocked by your rules (with the matching rule)
  - "allowed" means your rules let the request through, not that it reached the server
- **Static mode labels tracker references using the same `adblock` engine**, so both modes use one set of rules.

### Stretch (in priority order)
1. **"Does this page look like a brand?"** (Chromium mode, on demand): send a screenshot to Gemma, have it name the brand it sees, then let your code compare that with the domain. The optional image test in phase 1 shows whether this is practical on this CPU.
2. **Chat about the current page.** It's read-only, answers from the same snapshot, and streams its replies.
3. **Optional Gemini API switch** (~30 min). Local stays the default. It covers the Gemma track's "via the Gemini API" wording and is a fast fallback for image analysis.
4. **Audio:**
   - ElevenLabs read-aloud, opt-in and sending only text the user selects
   - a spoken warning when a page is flagged
   - ambient sound: a separate small Gemma call picks a mood from a fixed list, then `mpv` loops a matching free CC0 track from a local folder

### Not today
Logins, submitting forms, tabs, browser extensions, element-hiding rules, another browser engine. Lightpanda would be a separate integration, not a quick swap.

## Safety rules for the code
- **Fetching (both modes):**
  - only `http`/`https` for navigation, redirects, popups and subresources
  - block private, loopback and link-local IP addresses, which also protects Ollama on `localhost:11434`
  - keep TLS verification on
  - time and size limits on every load and on text extraction
  - no saved cookies
- **Redirects:**
  - **Static mode:** httpx with `follow_redirects=False`. Your own code follows each step, checks it against the rules, and stops after 5.
  - **Chromium mode:** Playwright only runs route handlers for the *first* URL of a redirect chain, so redirect steps are enforced by the local policy proxy instead.
- **Local policy proxy (`proxy.py`, Chromium mode):**
  - ~80 lines of asyncio on `127.0.0.1`, handling `CONNECT` and plain HTTP
  - for each connection it looks up the host, refuses private, loopback and link-local addresses and ports other than 80/443, then connects to **the exact IP it checked**; this also stops DNS rebinding
  - Chromium is launched with the proxy and a bypass list of `<-loopback>`. **This is essential:** Chromium skips the proxy for loopback addresses by default, which would let a redirect to `127.0.0.1` straight through.
  - Chromium is also launched with `--disable-quic` and `--force-webrtc-ip-handling-policy=disable_non_proxied_udp`
  - WebSockets go through the proxy like any other connection
  - optional: point httpx at the same proxy once it exists, to close DNS rebinding in static mode too
- **Chromium:**
  - one browser process; a fresh `new_context()` for every inspection, closed after capture
  - `context.route()` is installed **before any page is created**, so a popup's first request is covered too (`page.route()` misses it)
  - `accept_downloads=False`, `service_workers="block"`, no permissions granted
  - load until `domcontentloaded` plus a short fixed wait, or a page-specific condition for the demo pages; never use `networkidle`
  - cancel loads that have been replaced by a newer navigation
- **Filter lists:**
  - stored in `~/.cache/linkinspector/filters/` and downloaded by a `--update-filters` command, so they work offline
  - show each list's `! Version:` and `! Last modified:` in the UI
  - if no list can be loaded, show **"filtering unavailable"**; never quietly run with an empty filter
  - `filters/demo.txt` holds your own committed rules, such as `||tracker.test^`, so the blocking demo works even without the downloaded lists
- **Display:** never put page text into Textual markup strings. Use `Text()` or `rich.markup.escape()`, and strip terminal control characters.
- **Gemma requests:**
  - handled one at a time in a queue: security analysis first, then chat, then mood
  - each tagged with a page ID, so a slow answer can't land on the page you navigated to afterwards
- **Demo addresses:** `.test` hostnames map explicitly to local files and never touch the network: `MockTransport` in httpx, and `route.fulfill(path=...)` in Chromium.

## Local Gemma
**Laptop:** i7-1255U, 15 GB RAM, about 8 GB free, no GPU.

- **Model:** pin `gemma4:e2b-it-qat` (4.3 GB download, more in memory). Don't use `latest`, which points to the larger E4B.
- **Input:** a compact evidence packet containing URL, title, form details, code findings with their evidence IDs, and selected text.
  - start with a budget of about 500 tokens
  - show the user when input was cut short
- **Output:** `{assessment, reasons: [{evidence_ids, explanation}], claimed_brand, limitations}`
  - "insufficient information" is a valid assessment
  - your code checks the schema and that every evidence ID exists
  - valid JSON doesn't make an answer true or immune to prompt injection
- **Boundaries:**
  - **Gemma can only raise a warning, never clear one.** Code findings stay whatever Gemma says.
  - Gemma can't navigate, fill in forms, or change any setting.
  - system instructions stay separate from page content, and page text is never treated as instructions
- **Ollama settings:**
  - a bounded `num_ctx` (start with 2–4K); don't use the advertised maximum
  - short maximum output
  - `format` with a JSON schema
  - configurable `keep_alive`, and a warm-up request when the app starts
- **Measure, don't assume:** cold vs. warm speed, chat prompt-cache reuse, and memory with Chromium open. If it's slow, run the analysis only when asked.

## Stack
| Purpose | Package |
|---|---|
| Interface | `textual` |
| Static fetch | `httpx` |
| JavaScript mode and screenshots | `playwright` (bundled Chromium) |
| Policy proxy | Python `asyncio` (standard library) |
| HTML → readable Markdown | `trafilatura` |
| Domains | `tldextract` (built-in list copy, private suffixes on) |
| Lookalike characters | `confusable_homoglyphs` |
| Edit distance to brand list | `rapidfuzz` |
| Request filtering | `adblock` 0.6.0 (installed and tested on Python 3.12.3) |
| Local model | `ollama` |
| Tests | `pytest` |
| Text-to-speech (stretch) | `elevenlabs` |
| Audio playback | `mpv` (already installed) |

## Layout
```
app.py          Textual UI, navigation state, findings/coverage/AI/filtering status
fetch_static.py httpx static fetch
browser.py      Chromium lifecycle, per-inspection context, capture, page-change check, screenshots
proxy.py        local policy proxy: checks every Chromium connection (and each redirect step)
policy.py       URL/network rules, redirects, limits, .test mapping, route handler
blocking.py     adblock engine, filter list cache and --update-filters, list attribution
snapshot.py     snapshot and evidence types shared by both modes
url_checks.py   code-based URL checks (no network)
page_checks.py  forms, misleading links, resource references
render.py       readable Markdown, safe terminal text, resolved link table
llm.py          Ollama client, request queue, evidence prompts, validation, streaming
clipboard.py    desktop clipboard (wl-copy/wl-paste, xclip, xsel, pbcopy)
audio.py        ElevenLabs text-to-speech and mpv playback (stretch)
filters/        committed demo rules (demo.txt)
fixtures/       demo pages for .test hostnames and local request endpoints
spikes/         throwaway phase 1 test scripts
tests/          pytest tests
```

## Build order and pass conditions
About 7½ hours, not counting downloads. Start `ollama pull gemma4:e2b-it-qat` and `playwright install chromium` first and let them run in the background. If system libraries are missing, `sudo .venv/bin/playwright install-deps chromium`.

| # | Step | Time | Done when |
|---|---|---|---|
| 1 | Proof and benchmark | 45 min | Gemma answers a short evidence prompt (cold and warm timed); headless Chromium loads a demo page; `context.route()` with `adblock` blocks a demo tracker request; memory with both running is noted. The image prompt is optional and blocks nothing. If something fails, decide the fallback immediately. |
| 2 | Snapshot and reader (static mode) | 90 min | Demo pages render; links, back/forward and the redirect chain work; links only open through your handler. |
| 3 | Network rules and checks | 75 min | Redirect, domain and form demo pages give the expected findings; blocked actions and partial coverage are visible. |
| 4 | Gemma explanations | 60 min | Explanations cite evidence; with the model off, the reader and checks still work; stale answers never update a new page. |
| 5 | JavaScript mode in Chromium | 120 min | A JavaScript-only page renders; the request panel shows real blocks; a JavaScript-added form is detected from the same document; a redirect to loopback and a popup to a forbidden address never connect. |
| 6 | README, LICENSE, demo script, rehearsal | 60 min | Licence and list notices, setup steps, limitations; rehearsed with AI on, AI off, and WiFi off. |

Steps 1–4 are a complete project. The stretch goals only use time left over. If Chromium fails, ship static mode alone and say so; don't switch engines on the day. If time is short, static mode alone avoids all the Chromium network issues.

## Progress checklist
Tick `[x]` only when an item is done **and** verified. If an item is skipped, leave it unticked and add `— skipped: <reason>`. Fill in the blanks (`___`) with measured values.

### 0. Setup
- [x] Ollama installed
- [x] `gemma4:e2b-it-qat` pulled
- [x] Virtual environment created and packages installed (versions pinned in `requirements.txt`)
- [x] `playwright install chromium` done (Chromium 153 launches headless; `install-deps` not needed)
- [x] Filter lists downloaded to `~/.cache/linkinspector/filters/` (EasyList 202610111321, EasyPrivacy 202610111312)
- [x] File stubs from the layout created
- [x] LICENSE and `.gitignore` added
- [x] Committed

### 1. Proof and benchmark
- [x] `spikes/spike_llm.py`: an evidence-packet prompt returns JSON in the requested shape (only with `think=False`, see *Phase 1 results*)
- [x] Timings recorded: cold 55 s (13 s of that is model load), warm 37 s
- [x] Optional: one image prompt works: 43 s warm; correctly named "Northbank" from the screenshot
- [x] Model memory recorded (`ollama ps`): 3.9 GB
- [x] `spikes/spike_browser.py`: headless Chromium loads a `.test` page via `route.fulfill`
- [x] `adblock` in `context.route()` blocks a demo tracker request (rule from `filters/demo.txt`)
- [x] Request log, screenshot, and `response.text()` vs `page.content()` all work
- [x] Gemma and Chromium running together: free memory 4.0 GB
- [x] Decision: Gemma runs **automatically when the code checks found something**, and on a keypress otherwise
- [x] Decision: Chromium **in** (all browser checks passed; memory is fine)

#### Phase 1 results
- **Gemma 4 "thinks" by default.** Without `think=False`, hidden thinking used the whole 400-token output budget and the answer came back empty. Always pass `think=False`.
- **Output speed is the bottleneck:** about 8 tokens/s on this CPU, while a repeated prompt is read at 650+ tokens/s thanks to Ollama's prompt cache (29 tokens/s cold). A 270-token answer takes about 35 s. Ask for short explanations and cap `num_predict` at about 150 to aim for 15–20 s.
- **Gemma was too cautious:** it answered `insufficient_information` despite a lookalike domain plus an off-site password form. Its explanation was good. In phase 4, define each assessment level in the prompt.
- **Image prompt:** about 35 s just to read the screenshot. Fine on demand; too slow to run on every page.
- **`adblock`:** builds an engine from EasyList, EasyPrivacy and the demo rules in 0.1–0.2 s. Use `Engine(filters, optimize=False)` with `FilterSet(debug=True)` to get the exact matching rule (for example `.google-analytics.com/`); the optimised engine reports merged rules.
- **Memory:** about 6.4 GB free with Chromium alone, 4.0 GB with Chromium and the loaded model.

### 2. Reader (static mode)
- [x] `snapshot.py` dataclasses (snapshot, link, form, finding, request record)
- [x] First demo pages: article, lookalike login, redirect chain (plus a `home.test` start page)
- [x] `fetch_static.py`: `MockTransport` for `.test`, redirects followed by your own code, time/size limits, returns a snapshot
- [x] `render.py`: trafilatura Markdown, plus your own link table resolved against `<base>`
- [x] `app.py`: URL bar, Markdown view with `open_links=False`, link-click handler, back/forward, status bar
- [x] Pass: click through the demo pages; back/forward works (`tests/test_reader.py`, plus screenshots checked)
- [x] Committed

#### Phase 2 notes
- **Use `Markdown`, never `MarkdownViewer`.** `MarkdownViewer` handles link clicks itself and tries to open the link's path as a local file.
- **`Markdown.LinkClicked.href` arrives percent-decoded.** `app.resolve_link` maps it back to the page's link table to get the real destination.
- **The text extractor drops a heading that repeats the page title**, so the reader shows the title separately above the text.
- **Run it:** `.venv/bin/python app.py https://home.test/`. Tests: `.venv/bin/python -m pytest -q`.

### 3. Safety rules and checks
- [x] `policy.py`: `http`/`https` only, private IP block, redirect checks (max 5), connected to the static fetch
- [x] Brand list (25 brands → official domains, plus the made-up Northbank for the demo)
- [x] `url_checks.py`: `tldextract` (built-in list copy, private suffixes), punycode and lookalike characters, `rapidfuzz` distance, brand names in subdomains, bare IP hosts (plus `user@host` links)
- [x] `page_checks.py`: password forms, forms sending data to another site (single sign-on noted), passwords over plain HTTP, links whose text lies about their destination (plus a brand named in the title of a password page)
- [x] Every finding has an evidence ID, what was seen, and why it matters
- [x] Findings panel in the UI
- [x] Confirm bar before fetching (real destination plus URL warnings)
- [x] `pytest` tests for the checks pass (38 tests in total)
- [x] Committed

#### Phase 3 notes
- **When the confirm bar appears:** for typed or pasted links, only when the URL checks find a warning or danger. For clicked links, also whenever the link leaves the current site, so you always see the real destination first.
- **Brand matching avoids obvious false positives.** Substring and fuzzy (edit-distance) matches only apply to brand names of 6+ letters, so `pineapple.com` and `apply.com` aren't flagged as Apple. Short names still match through character swaps (`app1e`).
- **Network rules in static mode:** only ports 80/443; hosts are resolved and refused if any address is private, loopback, link-local, reserved or multicast (IPv4-mapped IPv6 included). A blocked redirect shows the address where it stopped.
- **Known gap:** httpx resolves the name again when it connects (DNS rebinding), as listed under Limitations.
- **The text extractor sometimes drops a page's main heading.** The title is shown separately, so nothing important is lost.

### 4. Gemma (finishes the minimum project)
- [x] `llm.py`: one-at-a-time queue, evidence packet, JSON schema, evidence ID check, page ID
- [x] Ollama settings: bounded `num_ctx` (4096), short maximum output (256 tokens), `keep_alive` (30 min), warm-up request, `think=False`
- [x] AI status label (analysing / done / unavailable) and explanation panel
- [x] Gemma can only raise a warning, never clear one
- [x] Ollama off: app still works and shows "unavailable"
- [x] Hidden-instructions demo page: code findings remain (`prize.test`: the injected text reaches Gemma, which still answers "likely phishing")
- [x] A slow answer never lands on a newer page (answers are stored per page; tested with a delayed fake client)
- [x] Committed and tagged `mvp`

#### Phase 4 notes
- **Measured in the app:** 17–33 s per answer. Gemma runs automatically when there's a warning or danger finding, and on **ctrl+g** (or **a**) otherwise.
- **Gemma's section sits right under the risk badge**, above the code findings. "Risk: HIGH" is the highest of the code findings and Gemma's assessment; when Gemma raised it, the badge says so.
- **Calibration:** at first Gemma called the legitimate Northbank page "likely phishing", just because it asks for a password. Three fixes:
  - a same-site password form is now **info**, not a warning
  - a new info finding notes when the page is on the brand's official site
  - the prompt now says those cases are normal

  Re-measured: Northbank gets "no extra concerns", single sign-on "no extra concerns", the PayPal lookalike and the voucher scam "likely phishing".
- **Answer checking:** reasons citing evidence IDs that don't exist are dropped. `PAGE` is allowed for something in the page text. Invalid JSON or a bad assessment shows "couldn't be used".
- **Tests use a fake Ollama client.** Gotcha: `app.workers.wait_for_complete([])` with an empty list waits for *every* worker, including Gemma's endless queue loop.

#### Cookie pop-ups
- **Reader view:** consent banners are removed before text extraction. They are found two ways, and either way the element must be small (≤ 3,000 characters) and mention cookies, so an article about baking cookies stays:
  - its name or role looks like a consent tool (OneTrust, Cookiebot, GOV.UK-style banners, dialogs…), and it talks about choices
  - it offers two or more cookie buttons ("Reject all", "Accept all", "Allow", "Decline"…). This catches banners with meaningless class names, like **Google's**. The *smallest* element around the buttons is taken, never a wrapper around a whole small page.
- **There is no accept/reject step in static mode, by design:** nothing is ever stored, so every visit works like "Reject all". The side panel's **Cookies** line says so, and lists what a hidden notice offered (e.g. "Reject all, Accept all"). Checked live: Google and GOV.UK both show their offered choices.
- **"Needs JavaScript" notice** at the top of the reader, when a page's `<noscript>` sends browsers without JavaScript elsewhere (Google's meta refresh to "click here if you are not redirected"), or when a page has scripts but almost no text (YouTube). That `<noscript>` text is dropped from the reader. **Google's homepage and search only work in JavaScript mode.**

#### Fixes from trying the app
- **Copy and paste in a VTE terminal (GNOME Terminal, Zorin) on Wayland.** Textual copies with an OSC 52 escape code, which VTE ignores, and its ctrl+v pastes only from Textual's own clipboard. `clipboard.py` uses `wl-copy`/`wl-paste`, falling back to `xclip`/`xsel` and `pbcopy`. Now:
  - **ctrl+v** in the address bar pastes from the desktop clipboard (ctrl+shift+v, the terminal's own paste, also works)
  - **ctrl+c** copies mouse-selected text, or the page address when nothing is selected
- **Search from the address bar:** words (or anything without a dot) go to DuckDuckGo's no-JavaScript results page, `html.duckduckgo.com`. Its results are rendered by our own code, because the text extractor dropped them.
- **Search engines' tracking redirects are unwrapped** in the link table (`duckduckgo.com/l/?uddg=…`, `google.com/url?q=…`). Links show and open their real destination, and search result pages don't trigger misleading-link warnings.
- **Markdown auto-linking is off.** Text that merely looks like a domain stays text; only links the page itself made can be clicked.
- **Security checks still see the full page:** links and forms come from the original HTML.
- **Coverage note** when banners were removed, and a **consent-wall note** when the page is mostly a consent notice or the host starts with `consent.`. linkinspector never accepts cookies, so it will always land on these.
- **Gemma's prompt** says cookie notices and cookie-choice forms aren't a phishing sign. Removing banners also stops them using up Gemma's 800-character page-text budget.
- **Checked on real sites:** GOV.UK's banner is removed. BBC's is added by JavaScript, so it doesn't appear in static mode.
- **Phase 5 (Chromium):** also load the **EasyList Cookie List** (download not done yet: `secure.fanboy.co.nz` did not resolve and the obvious mirrors returned 404, so find its current URL first), which blocks consent-tool scripts at the network level, and use `adblock`'s element-hiding rules for screenshots, so banners don't cover the page Gemma looks at.

### 5. JavaScript mode in Chromium
- [x] `blocking.py`: loads cached lists plus `filters/demo.txt`, shows version/date, shows "filtering unavailable" when lists are missing, `--update-filters` works (`python app.py --update-filters`; EasyList, EasyPrivacy and EasyList Cookie Notices)
- [x] `proxy.py`: `CONNECT` and plain HTTP, refuses private/loopback/link-local addresses and other ports, connects to the checked IP
- [x] Chromium launched with the proxy, bypass list `<-loopback>`, `--disable-quic`, WebRTC flag
- [x] `browser.py`: browser started when first needed; fresh session per inspection, closed after capture
- [x] Session settings: no downloads, service workers blocked, no permissions
- [x] `context.route()` installed before any page: `.test` answered from files, `adblock`, every decision logged
- [x] Snapshot built from the same load: original HTML plus final DOM
- [x] Page-change check: random marker plus a count of main-frame documents, one retry, otherwise "comparison unavailable"; JavaScript redirects recorded
- [x] Forms that only appear after JavaScript runs are flagged (warning alongside a danger finding, otherwise info)
- [x] Request panel (references / let through / blocked / refused kept separate)
- [x] **ctrl+r** (or **r**) switches JavaScript mode on and off and reloads the page
- [x] Test: 302 redirect to `127.0.0.1:<port>`; a dummy listener there counts zero connections (with a control run proving the listener does see unprotected connections)
- [x] Test: popup aimed at a forbidden address never connects
- [x] Pass: JavaScript-only demo page renders; panel shows real blocks (77 tests in total)
- [x] Committed

#### Phase 5 notes
- **Playwright never routes the next step of a redirect it was handed**, including redirects our own route handler fulfilled. So:
  - **real sites:** every redirect step goes through the proxy, which enforces the rules. This is exactly why the proxy exists, and it's tested with a real HTTP server.
  - **demo pages:** their own redirects (`short.test` → `hop.test` → `news.test`) are followed by linkinspector itself before Chromium navigates. They're local files, so this is equivalent.
- **Plain-HTTP refusals:** Chromium shows the proxy's 403 as if the site sent it, so the proxy adds an `x-linkinspector-refused` header with the reason, and the app reports a refusal instead of an empty 403 page.
- **Defence in depth:** private IP literals and `localhost` are refused by the route handler; names that *resolve* to local addresses (this laptop's own hostname → 127.0.1.1) are refused by the proxy.
- **Checked live:** google.com loads (12 Google telemetry requests blocked, cookie notice hidden); BBC News loads with 6 trackers blocked (scorecardresearch, chartbeat, dotmetrics…).
- **Google Search answers headless Chromium with a robot check (HTTP 429, CAPTCHA).** linkinspector doesn't try to get around it; it says so in the coverage notes. Searching from the address bar uses DuckDuckGo instead.
- **DuckDuckGo's no-JavaScript results page refuses headless Chromium** (HTTP 403, "If this persists, please email us") but serves static mode normally. So searches from the address bar are **always fetched in static mode**, even with JavaScript mode on (with a coverage note); results you open still use JavaScript mode.
- **Sites that refuse the headless browser:** a 401/403/429 with almost no content in JavaScript mode gets a coverage note suggesting static mode. linkinspector doesn't try to disguise itself further.
- **Reader layout for pages that aren't articles.** The text extractor is built for articles: it ran sign-in pages into one paragraph (AWS Builder ID) or kept a hidden alert instead of the form (GitHub). Our own renderer (`render.structured_markdown`) now handles these:
  - one line per block, with headings and list items
  - real links only; page text is escaped, so it can never become a fake link
  - form controls shown, like `[ Email: ______ ]`, `[ •••••• ]` and `[ Continue ]`
  - elements hidden by CSS skipped, which JavaScript mode marks before capture

  The extractor is still used when it finds a real article (500+ characters and at least a quarter of the page's text), with any password forms listed under it.
- **EasyList Cookie Notices** comes from uBlock Origin's up-to-date mirror (`ublockorigin.github.io/uAssets/thirdparties/easylist-cookies.txt`); the old fanboy host doesn't resolve and the adblockplus copy dates from March 2024.

### 6. Extras (in order, only with time left)
- [ ] Screenshot brand check
- [ ] Chat about the current page (streaming)
- [ ] Gemini API switch
- [ ] ElevenLabs read-aloud
- [ ] Spoken warning when a page is flagged
- [ ] Ambient sound

### 7. Wrap-up (stop adding features at __:__)
- [ ] README: what it is, setup, demo, limitations, licences and filter-list notices
- [ ] Demo script written
- [ ] Rehearsed with AI on
- [ ] Rehearsed with AI off
- [ ] Rehearsed with WiFi off
- [ ] Pushed, and repo made public
- [ ] Submitted

## Demo plan (Gemma 4 track)
- **Show Gemma doing the work:** explanations stream in on screen and cite specific evidence.
- **Turn WiFi off during the demo.** The `.test` pages are local and Gemma runs on the laptop, so it all keeps working: no cloud, no network, and it still catches the phish.
- **If the image check is built:** a page that looks like PayPal on a domain that isn't paypal.com.
- **Use only controlled demo pages.** No live phishing sites, and no real credentials collected.

## Demo pages and test cases
Tick when the demo page exists **and** the app behaves as expected on it.

- [x] A normal article, and a JavaScript-populated article (`news.test`, `spa.test`)
- [x] Normal single sign-on (should not be flagged as phishing): `intranet.test`, one info finding only
- [x] `paypa1` and Cyrillic lookalike domains: `paypa1-secure.test`, `pаypal.test` (`xn--pypal-4ve.test`)
- [x] A misleading subdomain (`paypal.com.account-verify.test`)
- [x] A link whose text lies about its destination (on `paypa1-secure.test`)
- [x] A login form added by JavaScript (`news.test` in JavaScript mode)
- [x] A page that navigates itself during capture (comparison marked unavailable, JavaScript redirect recorded): `jsredirect.test` settles on its second try; `bouncy.test` never settles
- [x] Hidden instructions aimed at the AI: code findings remain and the AI can't act. This doesn't prove the app resists every injection. (`prize.test`)
- [x] Cookie-consent banners removed from the reader view (`cookies.test`), and a consent wall reported (`video.test/watch` → `consent.test`)
- [x] A banner with meaningless class names, found by its "Reject all" / "Accept all" buttons (Google; `tests/test_static.py`)
- [ ] Cookie banners in JavaScript mode: EasyList Cookie List blocks consent scripts; screenshots aren't covered by a banner — list loaded and blocking (e.g. Cookiebot), and banners are removed from the reader text; screenshots not built yet
- [ ] Optional, JavaScript mode: a real per-inspection cookie choice. Click the banner's "Reject all" by default, or "Accept all" if the user picks it, before capturing; cookies are thrown away with the session. Only worth it for consent walls that hide content until you choose.
- [x] A page that requests a demo tracker, run with blocking on and off
- [x] Filter lists missing: "filtering unavailable" shown
- [x] A redirect chain (`short.test` → `hop.test` → `news.test`)
- [x] A redirect to a loopback address (Chromium mode: dummy listener sees zero connections; static mode: `bounce.test/local` is stopped at `127.0.0.1`)
- [x] Links to local network addresses (static: `bounce.test/router`, `localhost`, `2130706433`, `[::ffff:127.0.0.1]`; Chromium: popup, redirect and this laptop's own hostname)
- [x] A popup aimed at a forbidden address
- [x] Download attempts (refused in JavaScript mode; static mode never shows non-HTML)
- [ ] Slow loads, slow model, Ollama off — Ollama off done (`tests/test_llm.py`)
- [ ] Partial extraction, very large pages
- [ ] Markup and terminal escape codes in the page title
- [ ] Repeated navigation: memory stays bounded and no stale AI answers appear

## Limitations to state in the README
- **Fetching still contacts the site.** It reveals your IP address and can use up one-time links.
- **JavaScript mode runs the page's scripts.** Requests made before the AI analysis can't be undone.
- **Phishing kits can detect your browser and show it a clean page**, so a clean result isn't proof of safety.
- **Static mode only sees resources declared in the HTML.**
- **The policy proxy only covers traffic that goes through it.** WebRTC is reduced by a launch flag rather than fully closed. Static mode has a DNS-rebinding gap unless httpx is also pointed at the proxy.
- **Some sites refuse headless browsers.** Google Search shows a robot check (CAPTCHA) in JavaScript mode; linkinspector reports it rather than trying to get around it.
- **Code findings are observations, not verdicts.** Single sign-on, Unicode domains and IP hosts can all be legitimate.
- **Filter lists have their own licences** (EasyList/EasyPrivacy: GPLv3 / CC BY-SA 3.0). Credit them, and don't copy them into the repo.

## Similar projects
- **Browsh** (LGPL-2.1, last release v1.8.3 in January 2024) uses headless Firefox to draw pages in the terminal.
- **Carbonyl** (BSD, last release v0.0.3 in February 2023) does the same with Chromium.

Both show you the page. This project shows a readable version of the page plus link checks, tracker blocking, and a local AI explanation of the risks.
