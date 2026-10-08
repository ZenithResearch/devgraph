export function createDevgraphHttpTransport(options?:{origin?:string;fetch?:typeof fetch}): {
  getCapabilities():Promise<any>;
  getProviderProfile():Promise<any>;
  prepareCredential(value:any,options?:{signal?:AbortSignal}):Promise<any>;
  executeCredential(value:any,options?:{signal?:AbortSignal}):Promise<any>;
  reconcileOperation(value:any,options?:{signal?:AbortSignal}):Promise<any>;
};
