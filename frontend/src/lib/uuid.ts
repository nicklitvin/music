// A v4 UUID that also works off localhost over plain HTTP.
//
// `crypto.randomUUID` only exists in a secure context, so on a site served
// from http://<ip>/ it is simply undefined. Calling it there throws a
// TypeError, and when that happens inside an event handler before any state
// is set, the UI shows nothing at all -- which is how an upload came to
// silently do nothing in production while working locally.
//
// `crypto.getRandomValues` has no such restriction, so the fallback is still
// properly random; only the very last resort is not, and that is reserved
// for an environment with no Web Crypto whatsoever.
export function uuid(): string {
  const webCrypto = globalThis.crypto

  if (typeof webCrypto?.randomUUID === 'function') {
    return webCrypto.randomUUID()
  }

  if (typeof webCrypto?.getRandomValues === 'function') {
    const bytes = webCrypto.getRandomValues(new Uint8Array(16))
    bytes[6] = (bytes[6] & 0x0f) | 0x40 // version 4
    bytes[8] = (bytes[8] & 0x3f) | 0x80 // variant 10xx
    const hex = Array.from(bytes, (byte) => byte.toString(16).padStart(2, '0')).join('')
    return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`
  }

  // No Web Crypto at all. Not random enough to rely on, but these ids only
  // have to be unique within one browser session, and the alternative is
  // throwing.
  const random = () => Math.floor(Math.random() * 0x10000).toString(16).padStart(4, '0')
  return `${random()}${random()}-${random()}-4${random().slice(1)}-a${random().slice(1)}-${random()}${random()}${random()}`
}
