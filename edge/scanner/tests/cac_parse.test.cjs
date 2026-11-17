// Parser tests (PBI-12). SYNTHETIC DATA ONLY. Run:  node --test edge/scanner/tests/cac_parse.test.cjs
const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");
const fs = require("node:fs");
const { parseCac, maskEdipi, decodeEdipi, CacParseError } = require("../cac_parse.js");

const cards = JSON.parse(fs.readFileSync(path.join(__dirname, "synthetic_front.json"), "utf8"));
const normal = cards.filter((c) => !c.note);

test("every synthetic FRONT barcode gives the right DoD ID and name", () => {
  for (const c of normal) {
    const r = parseCac(c.front);
    assert.equal(r.side, "front");
    assert.equal(r.edipi, c.edipi);
    assert.equal(r.firstName, c.expect.firstName);
    assert.equal(r.middleInitial, c.expect.middleInitial);
    assert.equal(r.lastName, c.expect.lastName);
    assert.equal(r.category, "J");
    assert.equal(r.branch, "A");
  }
});

test("front and back of the same card give the same DoD ID", () => {
  for (const c of cards) {
    const back = parseCac(c.back);
    assert.equal(back.side, "back");
    assert.equal(back.edipi, c.edipi);
    assert.equal(back.edipi, parseCac(c.front).edipi);
    assert.equal(back.lastName, null);
  }
});

test("single-space layout ('First MLast') gives the same result", () => {
  for (const c of cards.filter((x) => x.front_variable)) {
    const a = parseCac(c.front), b = parseCac(c.front_variable);
    assert.deepEqual(b, a);
  }
});

test("padded fields: the middle initial is never glued onto the last name", () => {
  for (const c of cards) {
    const r = parseCac(c.front);
    assert.equal(r.lastName, c.expect.lastName);
    assert.equal(r.middleInitial, c.expect.middleInitial);
  }
});

test("edge cases: no middle initial, spaces in names, hyphenated name", () => {
  const byNote = Object.fromEntries(cards.filter((c) => c.note).map((c) => [c.note, c]));
  const noMi = parseCac(byNote["no middle initial"].front);
  assert.deepEqual([noMi.firstName, noMi.middleInitial, noMi.lastName], ["Kai", "", "Kilo"]);
  const spaced = parseCac(byNote["last name with spaces"].front);
  assert.deepEqual([spaced.firstName, spaced.middleInitial, spaced.lastName], ["Lane", "M", "De La Lima"]);
  const hyph = parseCac(byNote["hyphenated last name"].front);
  assert.equal(hyph.lastName, "Mike-Smith");
  const twoWordFirst = parseCac(byNote["first name with a space"].front);
  assert.deepEqual([twoWordFirst.firstName, twoWordFirst.middleInitial, twoWordFirst.lastName], ["Lee Ann", "O", "Oscar"]);
});

test("the parser never returns date of birth, card dates or card numbers", () => {
  const r = parseCac(normal[0].front);
  assert.deepEqual(Object.keys(r).sort(),
    ["branch", "category", "edipi", "firstName", "lastName", "middleInitial", "side"]);
});

test("base 32 DoD ID decoding, including a leading zero", () => {
  assert.equal(decodeEdipi("8C726G1"), "9000000001");
  assert.equal(decodeEdipi("0000001"), "0000000001");
  assert.throws(() => decodeEdipi("8C726GW"), CacParseError);   // W is not base 32
  assert.throws(() => decodeEdipi("0000000"), CacParseError);   // zero is not an ID
});

test("non-CAC and damaged barcodes are rejected with a reason that never quotes the scan", () => {
  const bad = [
    "", "012345678905", "https://example.com/menu", "PDF417_TEST_DATA_9000000001_END",
    normal[0].front.slice(0, 40),                        // cut off
    normal[0].front.slice(0, -37) + "X" + normal[0].front.slice(-36),   // layout broken
    "N" + normal[0].front.slice(1),                      // older format
    normal[0].back + "X",                                // back, wrong length
  ];
  for (const s of bad) {
    let err;
    assert.throws(() => parseCac(s), (e) => { err = e; return e instanceof CacParseError; });
    if (s.length > 4) assert.ok(!err.message.includes(s.slice(0, 12)), "error quoted the scan");
  }
});

test("masking shows only the last 4 digits", () => {
  assert.equal(maskEdipi("9000000001"), "******0001");
});
