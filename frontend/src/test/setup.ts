import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

// Vitest runs without injected globals here, so Testing Library's automatic
// teardown never registers. Unmount explicitly to keep one test's DOM out of
// the next one's queries.
afterEach(cleanup);
