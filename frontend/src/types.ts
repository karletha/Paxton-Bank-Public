export interface Capability {
  id: string;
  capabilityName: string;
  painPointLink: string;
  plainEnglishSummary: string;
  whyItMatters: string;
  sourceStatus: string;
  humanReviewRequired: boolean;
  confidence: number;
  groundingUrls: string[];
}

export type SessionState =
  | 'PRE_MEETING'
  | 'DISCOVERY'
  | 'SOLUTIONING_EDUCATION'
  | 'HUMAN_TAKEOVER'
  | 'OPPORTUNITY_ANALYSIS'
  | 'SALES_HANDOFF'
  | 'DEMO_COMPLETE';
