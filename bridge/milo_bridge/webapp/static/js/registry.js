// Adding a card = create js/cards/<name>.js + add one line here.
import status from "./cards/status.js";
import log from "./cards/log.js";
import camera from "./cards/camera.js";
import ears from "./cards/ears.js";
import voice from "./cards/voice.js";
import move from "./cards/move.js";
import poses from "./cards/poses.js";
import servos from "./cards/servos.js";
import sensors from "./cards/sensors.js";
import graph from "./cards/graph.js";

export const cards = [status, camera, move, ears, voice, poses, servos, sensors, graph, log];
