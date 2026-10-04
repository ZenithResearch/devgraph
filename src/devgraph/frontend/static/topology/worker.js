importScripts('/monitor/topology-assets/core.js');
onmessage = ({data}) => {
  const {generation,nodes,edges,positions,options}=data;
  const result=options.reveal?DevgraphTopology.revealLayout(nodes,new Map(positions),options):DevgraphTopology.layout(nodes,edges,new Map(positions),options);
  postMessage({generation,positions:result});
};
