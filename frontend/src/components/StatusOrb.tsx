export function StatusOrb({ label = 'Alles in Ordnung' }: { label?: string; detail?: string }) {
  return (
    <div className="sc-status-orb" aria-label={label}>
      <span className="sc-status-orb-core" />
    </div>
  );
}
