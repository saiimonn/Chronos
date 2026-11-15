// Application-level notifications that cross module boundaries without a shared
// store. The transport layer publishes session loss; pages publish term changes.
// Keeping the event names and the window guard in one place stops callers from
// hand-rolling either.

const UNAUTHORIZED_EVENT = "chronos:unauthorized";
const TERM_CHANGED_EVENT = "chronos:term-changed";

type Unsubscribe = () => void;

function publish(name: string): void {
  if (typeof window === "undefined") return;
  window.dispatchEvent(new Event(name));
}

function subscribe(name: string, handler: () => void): Unsubscribe {
  if (typeof window === "undefined") return () => {};
  window.addEventListener(name, handler);
  return () => window.removeEventListener(name, handler);
}

/** Announce that the local session is no longer valid. */
export function publishUnauthorized(): void {
  publish(UNAUTHORIZED_EVENT);
}

export function subscribeUnauthorized(handler: () => void): Unsubscribe {
  return subscribe(UNAUTHORIZED_EVENT, handler);
}

/** Announce that the active operational term may have changed. */
export function publishTermChanged(): void {
  publish(TERM_CHANGED_EVENT);
}

export function subscribeTermChanged(handler: () => void): Unsubscribe {
  return subscribe(TERM_CHANGED_EVENT, handler);
}
