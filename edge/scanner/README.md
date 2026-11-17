# Phone CAC scanner (PBI-12)

A web page that reads a CAC barcode with the phone's camera. It reads the **front
(PDF417)** barcode, and the back (Code 39) as a fallback. It pulls out the DoD ID and
name **on the phone**, and shows the name and the last 4 digits of the ID. It isn't
connected to the API yet. That's the next step, and it goes to the Gatekeeper over HTTPS.

| File | What it does |
|---|---|
| `index.html`, `scanner.css`, `scanner.js` | The page: camera, guide box, decode loop, result screen |
| `cac_parse.js` | Turns a scanned string into `{edipi, firstName, middleInitial, lastName, category, branch}`; never returns DOB or card dates |
| `vendor/zxing-wasm-3.1.5/` | The barcode reader (zxing-cpp compiled to WebAssembly, MIT license), kept in the repo so the page loads nothing from outside sites |
| `serve.py` | Small HTTPS server for the page, with a self-signed certificate |
| `tests/` | Parser tests on synthetic barcodes in the real CAC layouts |

## Privacy rules (what the code enforces)

- The raw barcode string is used once to parse, then dropped. It's **never shown, logged,
  saved, or sent**. `serve.py` only ever sees requests for the page's own files.
- The screen shows the name and `******1234`, then clears itself after 60 seconds. The
  camera stops as soon as the page is hidden.
- The page's security policy (sent by `serve.py`) blocks it from contacting **any** other
  site, so even a bug can't send a scan anywhere.
- Real CACs are scanned **only by their owner**, and nothing is saved (PBI-12's DoD change).
  Never paste a real barcode string into an issue, a chat, a test, or this repo.

## Run it on your phone

Phones only allow the camera on `https://` pages.

### Easiest: GitHub Pages (no laptop, any network)

The page is only static files. The camera and the barcode reading run on the phone, and
it never sends anything, so a static host is enough. AeroFlaire is public, so:

1. On github.com/AeroFlaire/MUSTER, go to **Settings**, then **Pages**.
2. Under **Build and deployment**, choose **Deploy from a branch**. Pick the branch with this
   folder (`main` once merged, or `arjo/pbi-12-front-scanner`) and **/ (root)**, then **Save**.
3. Wait about a minute. On the phone, open
   **`https://aeroflaire.github.io/MUSTER/edge/scanner/`**

You get a real certificate (no warning), and it works on Wi-Fi or cellular. GitHub only
serves the page's files. It never sees a scan, because the page has no code that sends one.
The security policy is also set inside `index.html`, so it applies on Pages too.

### From your laptop (if the network allows it)

```powershell
pip install cryptography            # used to make the self-signed certificate
python edge\scanner\serve.py
```

It prints addresses like `https://192.168.x.x:8443/`.

1. Put the phone and laptop on the **same network**. If the Army network blocks it, turn on
   the phone's hotspot and join it from the laptop.
2. Open the printed `https://...:8443/` address on the phone.
3. The phone warns about the certificate (it's self-signed). Accept it once:
   - **iPhone:** Show Details, then visit this website.
   - **Android Chrome:** Advanced, then Proceed.
4. Tap **Start camera** and allow the camera.
5. Hold the card flat, 4 to 6 inches away, with the wide barcode on the front inside the box.
   A tilt of up to about 15 degrees is fine.

**If the phone can't connect:**
- **Firewall:** an issued (government) laptop usually blocks all incoming connections by
  policy, and you can't change that. Use GitHub Pages instead.
- **Wrong address:** if the printed address changes between runs, a VPN or second adapter
  was picked. Run `ipconfig`, find the **Wireless LAN adapter Wi-Fi** "IPv4 Address", and
  use that with `:8443`.
- **Personal laptop:** allow Python on Private networks when Windows Firewall asks.

**Laptop webcam only:** `python edge\scanner\serve.py --http --host 127.0.0.1` and open
`http://localhost:8080/`. Browsers treat localhost as secure, so the camera works.

## Test cards (synthetic)

```powershell
pip install zxing-cpp reportlab pillow numpy
python scripts\make_front_test_cards.py      # -> test_front_cards.pdf (14 cards, front + back)
```

These are fake DoD IDs 9000000001–9000000014 in the real layouts. The last four are edge
cases: no middle initial, a last name with spaces, a hyphenated last name, and a first name
with a space. Print them,
or show the PDF on a second screen.

## Tests

```powershell
node --test edge\scanner\tests\cac_parse.test.cjs
```

What was checked before this was committed:

- **Parser tests:** 7 of 7 pass.
- **Real cards:** the parser was checked on two real cards. Front and back gave the same
  DoD ID, and the names were right. This was done locally, and the strings were not kept.
- **Printed test cards:** all 26 barcodes (13 fronts, 13 backs) decode.
- **Mobile Chromium with a simulated camera:**
  - Fronts read in 0.2–0.9 s, including cards tilted 6°, 10° and 16°.
  - A non-CAC PDF417 barcode is rejected, and scanning continues.
  - The page contacted only its own server, and no full DoD ID appeared on screen.

## Known limits

- **The front layout comes from real cards, not a published spec.** Version "M" is what
  current cards carry, and the older "N" format is rejected with a message. PBI-12 asks for
  5 cards. Each new owner should scan their own card and confirm the name and last 4.
- **Name fields:** real cards pad the names with spaces (first name 20 characters, middle
  initial 1, last name 26). Build 3 assumed single spaces, so the middle initial ended up glued
  to the last name. Build 4 reads the padded layout and still accepts single spaces. A name
  pasted into chat loses its padding, so chat copies aren't a reliable guide to the layout.
- **The front barcode also holds date of birth and card dates.** The parser ignores them, and
  nothing downstream should ever ask for them.

The earlier attempt (square-box page, a server that logged and forwarded the raw barcode,
and a tunnel program) was removed. `edge/scanner.html` now just redirects here. The header
shows **build 4**: if you don't see it, you're on an old copy of the page.
