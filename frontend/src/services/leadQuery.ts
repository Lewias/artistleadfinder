import type { LeadQuery } from './types';
export const defaultQuery: LeadQuery = {
  page: 1,
  page_size: 30,
  search: '',
  genre: '',
  status: '',
  source: '',
  minimum_score: 0,
  min_followers: 0,
  max_followers: 1000000000,
  activity_days: null,
  sort: 'score',
  descending: true,
};
