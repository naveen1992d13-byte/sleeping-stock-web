import { User, Upload, Package, FileSpreadsheet, ClipboardCheck, ClipboardList, BarChart3, Globe, Megaphone, HelpCircle, Smartphone, History, ScanLine, HardDrive } from 'lucide-react';

export const APPLICATION_MENU_ITEMS = [
  { path: '/users', label: 'User Hub', icon: User, id: 'users', adminOnly: true },
  { path: '/upload', label: 'Upload Center', icon: Upload, id: 'upload', adminOnly: true },
  { path: '/products', label: 'Product Hub', icon: Package, id: 'products' },
  { path: '/product-history', label: 'Product Hub History', icon: FileSpreadsheet, id: 'product-history', adminOnly: true },
  { path: '/orders', label: 'Order Desk', icon: ClipboardCheck, id: 'orders' },
  { path: '/order-history', label: 'Order History', icon: History, id: 'order-history', permissionLabel: 'Order Desk' },
  { path: '/requests', label: 'Request Center', icon: ClipboardList, id: 'requests' },
  { path: '/reports', label: 'Reports', icon: BarChart3, id: 'reports', adminOnly: true },
  { path: '/analytics', label: 'Analytics', icon: Globe, id: 'analytics', adminOnly: true },
  { path: '/storage-cost-monitor', label: 'Storage & Cost Monitor', icon: HardDrive, id: 'storage-cost-monitor', masterOnly: true },
  { path: '/notice-board', label: 'Notice Board', icon: Megaphone, id: 'dashboard' },
  { path: '/query', label: 'Query Desk', icon: HelpCircle, id: 'query', allRoles: true },
  { path: '/sleeping-stock-mobile', label: 'Sleeping Stock Mobile', icon: Smartphone, id: 'sleeping-stock-mobile', allRoles: true },
  { path: '/stock-audit', label: 'Stock Audit', icon: ScanLine, id: 'stock-audit', allRoles: true },
];

export const APPLICATION_PERMISSION_LABELS = APPLICATION_MENU_ITEMS
  .filter((item) => !item.masterOnly)
  .map((item) => item.permissionLabel || item.label);
