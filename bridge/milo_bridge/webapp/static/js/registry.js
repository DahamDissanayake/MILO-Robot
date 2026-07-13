// Adding a panel = create js/panels/<name>.js + add it to the right zone below.
import camera from "./panels/camera.js";
import move from "./panels/move.js";
import comm from "./panels/comm.js";
import sensors from "./panels/sensors.js";
import graph from "./panels/graph.js";
import poses from "./panels/poses.js";
import servos from "./panels/servos.js";
import log from "./panels/log.js";

export const registry = {
  cockpitCenter: [camera, move, comm],
  cockpitSide: [sensors],
  graph: [graph],
  tools: [poses, servos, log],
};
