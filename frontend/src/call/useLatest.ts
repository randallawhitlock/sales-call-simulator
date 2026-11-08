import { useCallback, useRef, useState } from 'react'

/** Server state that only the most recently started request may set. A slow response from an
 * older request (the screen's initial load, say) can't overwrite a newer one that a save
 * triggered while it was in flight. */
export function useLatest<T>(load: () => Promise<T>, initial: T): [T, () => Promise<void>] {
  const [value, setValue] = useState<T>(initial)
  const latest = useRef(0)
  const refresh = useCallback(async () => {
    const mine = ++latest.current
    const next = await load()
    if (mine === latest.current) setValue(next)
  }, [load])
  return [value, refresh]
}
