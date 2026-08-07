export function normalizePartNumber(value) {
  return String(value || '')
    .trim()
    .toUpperCase()
    .replace(/[^A-Z0-9]/g, '');
}

export function splitPartNumbers(value) {
  return [...new Set(
    String(value || '')
      .split(/[\s,;]+/)
      .map(normalizePartNumber)
      .filter(Boolean)
  )];
}

export function getAvailableQuantity(item) {
  return Number(
    item?.available_quantity ??
      item?.available_qty ??
      item?.quantity ??
      item?.qty ??
      0
  );
}

export function calculateVerification(systemQuantity, physicalQuantity, unitValue = 0) {
  const systemQty = Number(systemQuantity || 0);
  const physicalQty = Number(physicalQuantity || 0);
  const value = Number(unitValue || 0);
  const difference = physicalQty - systemQty;

  return {
    systemQty,
    physicalQty,
    status: difference === 0 ? 'MATCHED' : difference < 0 ? 'SHORTAGE' : 'EXCESS',
    shortageQty: difference < 0 ? Math.abs(difference) : 0,
    excessQty: difference > 0 ? difference : 0,
    differenceQty: difference,
    differenceValue: Math.abs(difference) * value,
  };
}
