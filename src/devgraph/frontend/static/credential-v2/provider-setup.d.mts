export function prepareProviderSetup(options:{provider:any;transport:any;origin:string;current?:()=>void}):Promise<{
  readonly profile:any;
  approve():Promise<{state:'approved'}>;
  connect():Promise<{state:'connected'}>;
}>;
