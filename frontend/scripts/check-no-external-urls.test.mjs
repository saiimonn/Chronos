import { describe, expect, it } from "vitest";
import { findExternalRefs } from "./check-no-external-urls.mjs";

describe("findExternalRefs", () => {
  it("flags external URLs in loading contexts", () => {
    expect(findExternalRefs("@import url('https://fonts.googleapis.com/css2?family=X');")).toEqual([
      "https://fonts.googleapis.com/css2?family=X",
    ]);
    expect(findExternalRefs('@import "https://cdn.example.com/a.css";')).toHaveLength(1);
    expect(findExternalRefs('@import"https://fonts.googleapis.com/css2?family=X";')).toHaveLength(1);
    expect(findExternalRefs("src:url(//fonts.gstatic.com/a.woff2)")).toHaveLength(1);
    expect(findExternalRefs('<script src="https://cdn.example.com/x.js"></script>')).toHaveLength(1);
    expect(findExternalRefs('jsx("a",{href:"https://example.com/"})')).toHaveLength(1);
    expect(findExternalRefs('fetch("https://api.example.com/v1")')).toHaveLength(1);
    expect(findExternalRefs("import('https://esm.sh/react')")).toHaveLength(1);
    expect(findExternalRefs('import{a}from"https://esm.sh/a";import"https://esm.sh/b"')).toHaveLength(2);
    expect(findExternalRefs('new WebSocket("wss://example.com/ws")')).toHaveLength(1);
    expect(findExternalRefs('"a{background:url(\\"https://evil.com/a.png\\")}"')).toHaveLength(1);
    expect(findExternalRefs('fetch("https:\\/\\/evil.com/x")')).toHaveLength(1);
    expect(findExternalRefs('{"src":"https://evil.com/i.png"}')).toHaveLength(1);
    expect(findExternalRefs('fetch("https://evil.com?@localhost/x")')).toHaveLength(1);
    expect(findExternalRefs('fetch("https://evil.com#@localhost")')).toHaveLength(1);
  });

  it("ignores namespaces, doc-link strings and LAN hosts", () => {
    expect(
      findExternalRefs(
        [
          'createElementNS("http://www.w3.org/2000/svg","svg")',
          '<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink">',
          '"Visit https://react.dev/errors/" + code',
          '"data from https://example.com is cached"',
          'const base="http://localhost:8000";fetch("http://localhost:8000/api")',
          'fetch("http://192.168.1.20:8000/api");fetch("http://10.0.0.5/x")',
          'fetch("http://localhost:8000?x=1");fetch("http://localhost#x")',
          'fetch("http://chronos.local/api");fetch("http://[fd00::1]:8000/x")',
          "src:url(./material-symbols-outlined.woff2)",
          'src="/assets/index.js"',
        ].join("\n"),
      ),
    ).toEqual([]);
  });
});
