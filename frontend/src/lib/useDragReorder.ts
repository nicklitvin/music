import { useCallback, useRef } from 'react'

interface Identifiable {
  id: string
}

// Pointer-based (mouse + touch, unlike native HTML5 drag-and-drop) reorder
// for a vertical list. The dragged item swaps ahead of/behind a neighbour
// as soon as the pointer crosses that neighbour's midpoint -- the standard
// reorder-on-hover feel -- and the final order is committed on release.
export function useDragReorder<T extends Identifiable>(
  setItems: React.Dispatch<React.SetStateAction<T[]>>,
  onCommit: (orderedIds: string[]) => void,
) {
  const itemRefs = useRef(new Map<string, HTMLElement>())

  const registerItemRef = useCallback((id: string, el: HTMLElement | null) => {
    if (el) itemRefs.current.set(id, el)
    else itemRefs.current.delete(id)
  }, [])

  const onHandlePointerDown = useCallback(
    (id: string) => (event: React.PointerEvent) => {
      event.preventDefault()
      const draggedEl = itemRefs.current.get(id)
      draggedEl?.classList.add('is-dragging')

      const onMove = (moveEvent: PointerEvent) => {
        const pointerY = moveEvent.clientY
        setItems((prev) => {
          const from = prev.findIndex((item) => item.id === id)
          if (from < 0) return prev

          let to = from
          prev.forEach((item, index) => {
            if (index === from) return
            const el = itemRefs.current.get(item.id)
            if (!el) return
            const rect = el.getBoundingClientRect()
            const mid = rect.top + rect.height / 2
            if (index < from && pointerY < mid) to = Math.min(to, index)
            if (index > from && pointerY > mid) to = Math.max(to, index)
          })

          if (to === from) return prev
          const next = [...prev]
          const [moved] = next.splice(from, 1)
          next.splice(to, 0, moved)
          return next
        })
      }

      const onUp = () => {
        window.removeEventListener('pointermove', onMove)
        window.removeEventListener('pointerup', onUp)
        draggedEl?.classList.remove('is-dragging')
        setItems((current) => {
          onCommit(current.map((item) => item.id))
          return current
        })
      }

      window.addEventListener('pointermove', onMove)
      window.addEventListener('pointerup', onUp)
    },
    [setItems, onCommit],
  )

  return { registerItemRef, onHandlePointerDown }
}
