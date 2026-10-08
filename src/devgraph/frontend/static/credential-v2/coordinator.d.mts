export class CredentialClientError extends Error { code: string; dispatched: boolean; }
export class DevgraphCredentialClient {
  constructor(options: {provider:any;transport:any;holderPublicKey:string;origin:string});
  execute(request:string,idempotencyKey:string,options?:{signal?:AbortSignal;reconcile?:boolean}):Promise<any>;
  reconcile(options?:{signal?:AbortSignal}):Promise<any>;
  dispose():void;
}
