// @types/node is not installed; tests that read source files from disk only need this one call.
declare module "node:fs" {
  export function readFileSync(path: string | URL, encoding: "utf8"): string;
}
