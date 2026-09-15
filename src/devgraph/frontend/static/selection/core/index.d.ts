/** Pure selection API. Work versions are decimal strings, never rounded JS i64 numbers. */
export interface Predictor { id: string; label?: string; unit: string; weight: number; reference: number; direction?: 1 | -1 }
export interface Goal { id: string; label: string; weight: number; scale: number; predictors: (Predictor & {direction: 1 | -1})[] }
export interface Resource { id: string; label: string; unit: string; conversion: number; scale: number; predictors: Predictor[] }
export type Model = {mode: 'linear_v1'} | {mode: 'custom_v1'; benefit: string; costs: Record<string, string>} | {mode: 'custom_net_v1'; expression: string};
export interface DecisionProfile { schema: 'devgraph.selection-profile.v1'; id: string; version: string; baseline: string; horizon: string; utility_unit: string; confidence_mode: 'none' | 'conservative_interpolation_v1'; blocks_policy: 'require' | 'unresolved'; quantization: number; goals: Goal[]; resources: Resource[]; model: Model }
export interface WorkNode { key: string; id: string; kind: 'Proposal' | 'Initiative' | 'Project' | 'Issue' | 'Task'; title: string; version: string | null; status: string }
export interface Dependency { dependent: string; prerequisite: string }
export interface Coverage { status: 'scanned' | 'unknown'; consistency: 'assembled' | 'fixture'; read_started_at: string; read_finished_at: string; unresolved: {work_key: string; reason: string; relationship?: string}[] }
export interface SelectionSnapshot { schema: 'devgraph.selection-snapshot.v1'; nodes: WorkNode[]; dependencies: Dependency[]; blocks: Dependency[]; hierarchy: {parent: string; child: string}[]; coverage: Coverage }
export interface Observation { point: number | null; adverse: number | null; confidence: number | null; unit: string; source: string; observed_at?: string; evidence_refs?: string[] }
export interface WorkObservations { context: {baseline: string; horizon: string}; work_version: string | null; values: Record<string, Observation> }
export interface Decision { schema: 'devgraph.selection-decision.v1'; profile: DecisionProfile; snapshot: SelectionSnapshot; seeds: string[]; observations: Record<string, WorkObservations> }
export interface Contribution { key: string; channel: 'benefit' | 'cost'; group: string; unit: string; point: number; adverse: number | null; effective: number; confidence: number | null; weight: number; source: string }
export interface Score { key: string; work_version: string; benefit: number; costs: {id: string; unit: string; conversion: number; amount: number}[]; burden: number; utility: number; goal_values: {id: string; benefit: number; weight: number}[]; contributions: Contribution[]; confidence_mode: DecisionProfile['confidence_mode']; model: Model['mode'] }
export interface Scope { nodes: WorkNode[]; dependencies: Dependency[]; added: string[] }
export interface Problem { key: string; title: string; code: string; message: string; details: unknown[] }
export interface Solution { network_edges?: (NetworkEdge & {flow: number; cut: boolean})[]; selected: string[]; quantized_utility: number; C: number; M: number; cut_capacity: number; scale: number; cut: {from: string; to: string; capacity: number; type: string}[]; weights: {key: string; weight: number}[]; solver: string; tie_policy: string; rounding: string }
export interface SelectionRun { schema: 'devgraph.selection-run.v1'; fingerprint: string; input: Decision; profile_version: string; rows: (Score & {selected: boolean; required_by: string[]})[]; totals: {utility: number; benefit: number; burden: number; costs: {id: string; unit: string; amount: number}[]}; scope: {added: string[]; keys: string[]; dependencies: Dependency[]}; solution: Solution }
export class SelectionError extends Error { constructor(code: string, message: string, details?: unknown[]); code: string; details: unknown[] }
export function canonicalJSON(value: unknown): string;
export function fingerprint(value: unknown): Promise<string>;
export function defaultProfile(): DecisionProfile;
export function bindings(profile: DecisionProfile): (Predictor & {key: string; channel: 'benefit' | 'cost'; group: string})[];
export function validateProfile(value: unknown): DecisionProfile;
export function validateSnapshot(value: unknown): SelectionSnapshot;
export function validateDecision(value: unknown): Decision;
export function fromMonitorSnapshot(value: unknown): SelectionSnapshot;
export function resolveScope(snapshot: SelectionSnapshot, seeds: string[], profile: DecisionProfile): Scope;
export function evaluateExpression(source: string, variables: Record<string, number>): number;
export function evaluateScore(node: WorkNode, observations: WorkObservations | undefined, profile: DecisionProfile): Score;
export function solveClosure(nodes: {key: string; utility: number}[], dependencies: Dependency[], scale?: number): Solution;
export function evaluateDecision(input: Decision): {scope: Scope; scores: Score[]; problems: Problem[]};
export function runSelection(input: Decision): Promise<SelectionRun>;

export interface NetworkEdge {id: string; from: string; to: string; capacity: number; type: 'benefit' | 'cost' | 'prerequisite'}
export interface CompiledNetwork {C: number; M: number; scale: number; weights: {key: string; weight: number}[]; edges: NetworkEdge[]}
export function compileNetwork(nodes: {key: string; utility: number}[], dependencies: Dependency[], scale?: number): CompiledNetwork;
export interface NetworkVariable extends Predictor {key: string; group: string; channel: 'benefit' | 'cost'; point: number | null; adverse: number | null; confidence: number | null; source: string; observed_unit: string | null; required_fields: string[]}
export interface NetworkDraft {schema: 'devgraph.selection-network.v1'; status: 'ready' | 'needs_inputs'; scale: number; terminals: {key: string; kind: 'source' | 'sink'}[]; nodes: {key: string; work: WorkNode; observation_work_version: string | null; observation_context: {baseline: string; horizon: string} | null; variables: NetworkVariable[]; score: Score | null; problem: {code: string; message: string; details: unknown[]} | null}[]; edges: (Omit<NetworkEdge, 'capacity'> & {capacity: number | null; expression?: string})[]; C: number | null; M: number | null}
export function initializeDecision(snapshot: SelectionSnapshot, profile: DecisionProfile, seeds: string[]): Decision;
export function describeNetwork(input: {nodes: WorkNode[]; dependencies: Dependency[]; profile: DecisionProfile; observations?: Record<string, WorkObservations>}): NetworkDraft;
