importScripts('/monitor/topology-assets/core.js');
onmessage = ({data}) => {
  const {generation,nodes,edges,positions,options}=data;
  const result=DevgraphTopology.layout(nodes,edges,new Map(positions),options);
  postMessage({generation,positions:result});
};
