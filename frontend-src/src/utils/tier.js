// daisyUI badge classes per account tier. Lives outside the component files so
// they can share it without breaking React fast-refresh (a component file must
// only export components).
export const TIER_BADGE = {
  admin: 'badge-primary',
  premium: 'badge-accent',
  free: 'badge-ghost border-base-content/20',
};
