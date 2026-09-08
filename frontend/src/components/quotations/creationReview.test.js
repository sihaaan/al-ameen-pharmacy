import quotationAPI from '../../api/quotations';
import { reviewProductCreation } from './creationReview';

jest.mock('../../api/quotations', () => ({ __esModule: true, default: { items: { creationReview: jest.fn() } } }));

beforeEach(() => jest.clearAllMocks());

test('reviews a large inquiry in bounded batches and keeps results tied to row IDs', async () => {
  const rows = Array.from({ length: 45 }, (_, id) => ({ id, name: `Item ${id}` }));
  quotationAPI.items.creationReview.mockImplementation(async ({ rows: batch }) => ({ data: {
    results: batch.slice().reverse().map((row) => ({ id: row.id, standard_name: `Matched ${row.id}`, ai_status: 'ai_matched' })),
  } }));
  const reviewed = await reviewProductCreation(rows, 7);
  expect(quotationAPI.items.creationReview.mock.calls.map(([payload]) => payload.rows.length)).toEqual([20, 20, 5]);
  expect(reviewed[22].standard_name).toBe('Matched 22');
  expect(quotationAPI.items.creationReview).toHaveBeenCalledWith(expect.objectContaining({ company: 7 }));
});

test('AI outage preserves the item and leaves ordinary creation checks available', async () => {
  quotationAPI.items.creationReview.mockRejectedValue(new Error('Unavailable'));
  const reviewed = await reviewProductCreation([{ id: 9, name: 'Original customer item' }], 7);
  expect(reviewed[9]).toEqual(expect.objectContaining({ standard_name: 'Original customer item', ai_status: 'lookup_failed' }));
});
