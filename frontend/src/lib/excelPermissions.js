/** Excel/ZIP export permission helper — mirrors backend excel_permissions.py */
export function canExportExcel(user) {
  const role = (user?.role || '').toLowerCase();
  return role === 'master' || role === 'admin';
}
