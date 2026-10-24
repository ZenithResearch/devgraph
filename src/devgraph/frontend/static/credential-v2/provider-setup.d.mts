export function prepareProviderSetup(options:{provider:any;transport:any;origin:string;requiredCapability?:'credential_presentation_v2'|'read_access_v1';current?:()=>void}):Promise<{
  readonly profile:any;
  readonly accessRequests:boolean;
  readonly graphAccessV2:boolean;
  readonly setupStatus:null|(()=>Promise<{schema:'castalia.read-setup-status.v1';connection_approved:boolean;profile_approved:boolean}>);
  approve():Promise<{state:'approved'}>;
  connect():Promise<{state:'connected'}>;
}>;
