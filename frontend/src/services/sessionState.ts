export type SessionState =
  | 'PRE_MEETING'
  | 'DISCOVERY'
  | 'SOLUTIONING_EDUCATION'
  | 'HUMAN_TAKEOVER'
  | 'OPPORTUNITY_ANALYSIS'
  | 'SALES_HANDOFF'
  | 'DEMO_COMPLETE';

export interface DiscoveryMilestones {
  expansion_discussed: boolean;
  complexity_revealed: boolean;
  pain_points_identified: boolean;
}

export interface SolutioningMilestones {
  needs_confirmed: boolean;
  solutions_proposed: boolean;
  next_steps_outlined: boolean;
}

export interface ProspectProfile {
  companyName: string;
  contactName: string;
  hasExpansionHK: boolean;
  hasExpansionSG: boolean;
  industry: string;
  existingSetup: string;
}

export interface AppState {
  sessionState: SessionState;
  discoveryMilestones: DiscoveryMilestones;
  solutioningMilestones: SolutioningMilestones;
  isLiveConnected: boolean;
  prospectProfile: ProspectProfile;
  restrictedTriggerTopic: string | null;
}

export const defaultProspectProfile: ProspectProfile = {
  companyName: 'Prospective Corporate Client',
  contactName: 'Executive Client',
  hasExpansionHK: false,
  hasExpansionSG: false,
  industry: 'Commercial Client',
  existingSetup: 'Pending live discovery discussion',
};

export const initialAppState: AppState = {
  sessionState: 'DISCOVERY',
  discoveryMilestones: {
    expansion_discussed: false,
    complexity_revealed: false,
    pain_points_identified: false,
  },
  solutioningMilestones: {
    needs_confirmed: false,
    solutions_proposed: false,
    next_steps_outlined: false,
  },
  isLiveConnected: true,
  prospectProfile: defaultProspectProfile,
  restrictedTriggerTopic: null,
};
