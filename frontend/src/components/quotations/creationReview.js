import quotationAPI from '../../api/quotations';

// One AI request for 20 rows; no per-item dialogs or owner approval.
export async function reviewProductCreation(rows, company) {
  const results = [];
  for (let offset = 0; offset < rows.length; offset += 20) {
    const batch = rows.slice(offset, offset + 20);
    try {
      const response = await quotationAPI.items.creationReview({ rows: batch, company });
      const reviewed = response.data?.results || [];
      batch.forEach((row) => results.push(reviewed.find((item) => String(item.id) === String(row.id)) || {
        ...row, standard_name: row.name, ai_status: 'lookup_failed', reason: 'AI check unavailable; catalogue checks still apply.',
      }));
    } catch {
      batch.forEach((row) => results.push({ ...row, standard_name: row.name, ai_status: 'lookup_failed',
        reason: 'AI check unavailable; catalogue checks still apply.' }));
    }
  }
  return Object.fromEntries(results.map((row) => [row.id, row]));
}
