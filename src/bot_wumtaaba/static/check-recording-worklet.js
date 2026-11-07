// Capture mono PCM directly; outputs remain silent to avoid microphone feedback.
class PCMRecorder extends AudioWorkletProcessor {
 constructor(){super();this.samples=new Float32Array(4096);this.used=0;this.stopped=false;this.port.onmessage=({data})=>{if(data.stop){this.stopped=true;this.flush(true);}};}
 flush(done=false){const samples=this.samples.slice(0,this.used);this.port.postMessage({samples,done},[samples.buffer]);this.used=0;}
 process(inputs){if(this.stopped)return false;const channels=inputs[0];if(!channels?.length)return true;for(let i=0;i<channels[0].length;i++){let sample=0;for(const channel of channels)sample+=channel[i];this.samples[this.used++]=sample/channels.length;if(this.used===this.samples.length)this.flush();}return true;}
}
registerProcessor('pcm-recorder',PCMRecorder);
