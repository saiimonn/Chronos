// Catalog records (subjects, rooms, buildings) are never deleted: past
// schedules, history and mined rules name them. They are deactivated instead,
// hidden behind "Show inactive", and activated again from the same row
// (see CatalogTable). These pieces keep that the same on every catalog page.

/** A record is active unless the server said otherwise. */
export function isActive(record: { is_active?: boolean }): boolean {
  return record.is_active !== false;
}

export function ShowInactiveToggle({ checked, onChange, count }: {
  checked: boolean;
  onChange: (checked: boolean) => void;
  count: number;
}) {
  return (
    <label className="flex items-center gap-2 text-sm text-on-surface-variant cursor-pointer">
      <input type="checkbox" checked={checked} onChange={(event) => onChange(event.target.checked)} />
      Show inactive ({count})
    </label>
  );
}

export function InactiveBadge() {
  return (
    <span className="h-fit px-2 py-1 rounded border border-outline-variant text-on-surface-variant text-xs font-label-md uppercase">
      Inactive
    </span>
  );
}

/** What the empty state says when only hidden (inactive) records match. */
export function hiddenInactiveMessage(noun: string): string {
  return `Only inactive ${noun} match. Tick "Show inactive" to see them.`;
}
