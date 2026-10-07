// A soft gate in front of the library.
//
// This is NOT access control: the token below ships inside the JavaScript
// bundle, so anyone who opens devtools can read it, and anyone who sets the
// storage key below walks straight in. It keeps casual visitors out of a
// personal app; it does not protect anything. Real protection would mean
// the backend refusing to serve without a credential it verifies.
// Exported so tests don't each hardcode their own copy and drift when it
// changes. It is not a secret in any meaningful sense -- see above.
export const ACCESS_TOKEN = '1010'
const STORAGE_KEY = 'access-granted'

export function isSignedIn(): boolean {
  try {
    return localStorage.getItem(STORAGE_KEY) === ACCESS_TOKEN
  } catch {
    // Private browsing or storage disabled: no stored grant, so the gate
    // simply asks again rather than erroring.
    return false
  }
}

/** Checks the token and remembers it on success. Returns whether it was right. */
export function signIn(token: string): boolean {
  if (token.trim() !== ACCESS_TOKEN) return false
  try {
    localStorage.setItem(STORAGE_KEY, ACCESS_TOKEN)
  } catch {
    // Can't remember it; the session still proceeds, it just asks again
    // next time.
  }
  return true
}

export function signOut(): void {
  try {
    localStorage.removeItem(STORAGE_KEY)
  } catch {
    // Nothing stored to begin with.
  }
}
