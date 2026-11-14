/**
 * The rubric editor's saves still under way, by session, each resolving to whether it was saved. "Start research"
 * waits for them, so a search never reads the rubric before the shopper's last change has reached it (US-09), and
 * never starts on a rubric whose last change failed to save.
 */
const pending = new Map<string, Set<Promise<boolean>>>();

/** Records a save (or a leave-out being read and then saved) until it settles. */
export function trackRubricSave(sessionId: string, save: Promise<boolean>) {
  const saves = pending.get(sessionId) ?? new Set<Promise<boolean>>();
  saves.add(save);
  pending.set(sessionId, saves);
  void save
    .catch(() => false)
    .finally(() => {
      saves.delete(save);
      if (saves.size === 0 && pending.get(sessionId) === saves) pending.delete(sessionId);
    });
}

/** True once every save under way has landed; false when one failed (the editor has said so and rolled it back). */
export async function rubricSaved(sessionId: string): Promise<boolean> {
  const saves = pending.get(sessionId);
  if (!saves || saves.size === 0) return true;
  const results = await Promise.all([...saves].map((s) => s.catch(() => false)));
  return results.every(Boolean);
}
