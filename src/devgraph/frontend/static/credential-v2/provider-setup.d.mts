export function prepareProviderSetup(options:{provider:any;transport:any;origin:string;requiredCapability?:'credential_presentation_v2'|'read_access_v1';current?:()=>void}):Promise<{
  readonly profile:any;
  approve():Promise<{state:'approved'}>;
  connect():Promise<{state:'connected'}>;
}>;
