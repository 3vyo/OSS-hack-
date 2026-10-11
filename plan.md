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
proxy.py        local policy proxy: checks every Chromium connection
policy.py       URL/network rules, redirects, limits, .test mapping, route handler
blocking.py     adblock engine, filter list cache and --update-filters, list attribution
snapshot.py     snapshot and evidence types shared by both modes
url_checks.py   code-based URL checks (no network)
page_checks.py  forms, misleading links, resource references
render.py       readable Markdown, safe terminal text, resolved link table
llm.py          Ollama client, request queue, evidence prompts, validation, streaming
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
- [ ] `policy.py`: `http`/`https` only, private IP block, redirect checks (max 5), connected to the static fetch
- [ ] Brand list (about 20 brands → official domains)
- [ ] `url_checks.py`: `tldextract` (built-in list copy, private suffixes), punycode and lookalike characters, `rapidfuzz` distance, brand names in subdomains, bare IP hosts
- [ ] `page_checks.py`: password forms, forms sending data to another site (single sign-on noted), passwords over plain HTTP, links whose text lies about their destination
- [ ] Every finding has an evidence ID, what was seen, and why it matters
- [ ] Findings panel in the UI
- [ ] Confirm bar before fetching (real destination plus URL warnings)
- [ ] `pytest` tests for the checks pass
- [ ] Committed

### 4. Gemma (finishes the minimum project)
- [ ] `llm.py`: one-at-a-time queue, evidence packet, JSON schema, evidence ID check, page ID
- [ ] Ollama settings: bounded `num_ctx`, short maximum output, `keep_alive`, warm-up request
- [ ] AI status label (analysing / done / unavailable) and explanation panel
- [ ] Gemma can only raise a warning, never clear one
- [ ] Ollama off: app still works and shows "unavailable"
- [ ] Hidden-instructions demo page: code findings remain
- [ ] A slow answer never lands on a newer page
- [ ] Committed and tagged `mvp`

### 5. JavaScript mode in Chromium
- [ ] `blocking.py`: loads cached lists plus `filters/demo.txt`, shows version/date, shows "filtering unavailable" when lists are missing, `--update-filters` works
- [ ] `proxy.py`: `CONNECT` and plain HTTP, refuses private/loopback/link-local addresses and other ports, connects to the checked IP
- [ ] Chromium launched with the proxy, bypass list `<-loopback>`, `--disable-quic`, WebRTC flag
- [ ] `browser.py`: browser started when first needed; fresh session per inspection, closed after capture
- [ ] Session settings: no downloads, service workers blocked, no permissions
- [ ] `context.route()` installed before any page: `.test` answered from files, `adblock`, every decision logged
- [ ] Snapshot built from the same load: original HTML plus final DOM
- [ ] Page-change check: marker plus `framenavigated`, one retry, otherwise "comparison unavailable"; JavaScript redirects recorded
- [ ] Forms that only appear after JavaScript runs are flagged
- [ ] Request panel (references / seen / blocked kept separate)
- [ ] `r` key switches to JavaScript mode
- [ ] Test: 302 redirect to `127.0.0.1:<port>`; a dummy listener there counts zero connections
- [ ] Test: popup aimed at a forbidden address never connects
- [ ] Pass: JavaScript-only demo page renders; panel shows real blocks
- [ ] Committed

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

- [ ] A normal article, and a JavaScript-populated article
- [ ] Normal single sign-on (should not be flagged as phishing)
- [ ] `paypa1` and Cyrillic lookalike domains
- [ ] A misleading subdomain (`paypal.com.account-verify.example`)
- [ ] A link whose text lies about its destination
- [ ] A login form added by JavaScript
- [ ] A page that navigates itself during capture (comparison marked unavailable, JavaScript redirect recorded)
- [ ] Hidden instructions aimed at the AI: code findings remain and the AI can't act. This doesn't prove the app resists every injection.
- [ ] A page that requests a demo tracker, run with blocking on and off
- [ ] Filter lists missing: "filtering unavailable" shown
- [x] A redirect chain (`short.test` → `hop.test` → `news.test`)
- [ ] A redirect to a loopback address (Chromium mode: dummy listener sees zero connections)
- [ ] Links to local network addresses
- [ ] A popup aimed at a forbidden address
- [ ] Download attempts
- [ ] Slow loads, slow model, Ollama off
- [ ] Partial extraction, very large pages
- [ ] Markup and terminal escape codes in the page title
- [ ] Repeated navigation: memory stays bounded and no stale AI answers appear

## Limitations to state in the README
- **Fetching still contacts the site.** It reveals your IP address and can use up one-time links.
- **JavaScript mode runs the page's scripts.** Requests made before the AI analysis can't be undone.
- **Phishing kits can detect your browser and show it a clean page**, so a clean result isn't proof of safety.
- **Static mode only sees resources declared in the HTML.**
- **The policy proxy only covers traffic that goes through it.** WebRTC is reduced by a launch flag rather than fully closed. Static mode has a DNS-rebinding gap unless httpx is also pointed at the proxy.
- **Code findings are observations, not verdicts.** Single sign-on, Unicode domains and IP hosts can all be legitimate.
- **Filter lists have their own licences** (EasyList/EasyPrivacy: GPLv3 / CC BY-SA 3.0). Credit them, and don't copy them into the repo.

## Similar projects
- **Browsh** (LGPL-2.1, last release v1.8.3 in January 2024) uses headless Firefox to draw pages in the terminal.
- **Carbonyl** (BSD, last release v0.0.3 in February 2023) does the same with Chromium.

Both show you the page. This project shows a readable version of the page plus link checks, tracker blocking, and a local AI explanation of the risks.
