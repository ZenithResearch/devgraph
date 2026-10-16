export class CredentialClientError extends Error { code: string; dispatched: boolean; }
export class DevgraphCredentialClient {
  constructor(options: {provider:any;transport:any;holderPublicKey:string;origin:string});
  execute(request:string,idempotencyKey:string,options?:{signal?:AbortSignal;reconcile?:boolean;validateResult?:(response:any,reconcile:boolean)=>any}):Promise<any>;
  reconcile(options?:{signal?:AbortSignal;validateResult?:(response:any,reconcile:boolean)=>any}):Promise<any>;
  dispose():void;
}
