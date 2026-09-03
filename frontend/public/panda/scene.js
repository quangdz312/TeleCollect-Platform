// Franka Panda pick-and-place loop for the login screen.
//
// Adapted from the standalone hero page. Two differences from that version:
// the scene builds into a caller-supplied canvas and returns a teardown
// function (React unmounts this), and the palette is the app's blue ramp from
// globals.css rather than the hero's purple.
//
// Loaded from /public over a plain <script type="module"> import so Three.js
// and the 10 GLB meshes -- 3.6 MB -- stay out of the Next bundle and off the
// critical path. Nothing here is imported by the app at build time.

import * as THREE from './three.module.min.js';
import { GLTFLoader } from './GLTFLoader.js';
import { MeshoptDecoder } from './meshopt_decoder.module.js';
import { pandaMotion } from './panda-motion.js';
import { sampleCycle } from './motion-player.js';

// Matches --color-accent-500 / --color-ink-* in app/globals.css.
const ACCENT = 0x2563eb;
const CYAN = 0x0891b2;
const SURFACE = 0xf6f8fc;
const JOINT = 0x334155;

export function mountPanda(canvas, { onReady, onError } = {}) {
  const reduceMotion = matchMedia('(prefers-reduced-motion: reduce)').matches;
  let disposed = false;
  let frame = 0;

  const scene = new THREE.Scene();
  // Closer and lower than the standalone hero page: there the arm shared a
  // full-height column, here it sits beside body copy, so the table has to give
  // up frame to the arm or the arm reads as a detail on a big empty slab.
  const camera = new THREE.PerspectiveCamera(30, 1, 0.01, 100);
  camera.position.set(1.85, 1.26, 2.08);

  const renderer = new THREE.WebGLRenderer({
    canvas,
    antialias: true,
    alpha: true,
    powerPreference: 'high-performance',
  });
  renderer.setPixelRatio(Math.min(devicePixelRatio, 1.75));
  renderer.setClearColor(0x000000, 0);
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 0.92;

  scene.add(new THREE.HemisphereLight(0xffffff, 0xc2cfe4, 1.35));
  const key = new THREE.DirectionalLight(0xffffff, 2.0);
  key.position.set(3, 6, 4);
  scene.add(key);
  const fill = new THREE.DirectionalLight(0x7aa2f7, 0.58);
  fill.position.set(-4, 2, -3);
  scene.add(fill);

  const whiteMaterial = new THREE.MeshStandardMaterial({
    color: SURFACE, roughness: 0.82, metalness: 0.01, side: THREE.DoubleSide,
  });
  const jointMaterial = new THREE.MeshStandardMaterial({
    color: JOINT, roughness: 0.86, metalness: 0.03, side: THREE.DoubleSide,
  });
  const outlineMaterial = new THREE.LineBasicMaterial({
    color: ACCENT, transparent: true, opacity: 0.5, toneMapped: false,
  });

  function styleModel(root, partName) {
    root.traverse((object) => {
      if (!object.isMesh || !object.geometry) return;
      object.material = partName === 'finger' ? jointMaterial : whiteMaterial;
      object.geometry.deleteAttribute('normal');
      object.geometry.computeVertexNormals();
      const edges = new THREE.LineSegments(
        new THREE.EdgesGeometry(object.geometry, 38), outlineMaterial,
      );
      edges.scale.setScalar(1.0014);
      object.add(edges);
    });
    return root;
  }

  const loader = new GLTFLoader();
  loader.setMeshoptDecoder(MeshoptDecoder);
  const loadPart = (name) => new Promise((resolve, reject) => {
    loader.load(`./panda/mesh/${name}.glb`, (result) => {
      // glTF is Y-up. Undo that per-mesh because the whole URDF hierarchy is
      // converted from Z-up once, below.
      result.scene.rotation.set(Math.PI / 2, 0, 0);
      resolve(styleModel(result.scene, name));
    }, undefined, reject);
  });

  const motionRoot = new THREE.Group();
  motionRoot.rotation.y = -0.3;
  motionRoot.position.set(-0.1, 0, 0.03);
  scene.add(motionRoot);

  const robotZUp = new THREE.Group();
  robotZUp.rotation.x = -Math.PI / 2;
  motionRoot.add(robotZUp);

  const joints = [];
  function addJoint(parent, position, rpy, mesh) {
    const origin = new THREE.Group();
    origin.position.set(...position);
    origin.rotation.set(...rpy, 'XYZ');
    parent.add(origin);
    const pivot = new THREE.Group();
    origin.add(pivot);
    pivot.add(mesh);
    joints.push(pivot);
    return pivot;
  }

  let toolTip = null;
  let leftFinger = null;
  let rightFinger = null;

  // --- table -------------------------------------------------------------
  const platform = new THREE.Group();
  platform.position.set(0.03, 0, 0.02);
  scene.add(platform);
  const platformGeometry = new THREE.BoxGeometry(1.78, 0.07, 1.2);
  const platformMesh = new THREE.Mesh(platformGeometry, new THREE.MeshStandardMaterial({
    color: 0xfbfcff, roughness: 0.94, metalness: 0,
  }));
  platformMesh.position.y = -0.035;
  platform.add(platformMesh);
  const platformEdges = new THREE.LineSegments(
    new THREE.EdgesGeometry(platformGeometry), outlineMaterial,
  );
  platformEdges.position.y = -0.035;
  platform.add(platformEdges);

  const gridVertices = [];
  for (let x = -0.84; x <= 0.841; x += 0.12) gridVertices.push(x, 0.002, -0.55, x, 0.002, 0.55);
  for (let z = -0.55; z <= 0.551; z += 0.11) gridVertices.push(-0.84, 0.002, z, 0.84, 0.002, z);
  const gridGeometry = new THREE.BufferGeometry();
  gridGeometry.setAttribute('position', new THREE.Float32BufferAttribute(gridVertices, 3));
  platform.add(new THREE.LineSegments(gridGeometry, new THREE.LineBasicMaterial({
    color: 0x7d9bd4, transparent: true, opacity: 0.22, depthWrite: false,
  })));

  for (const [x, z] of [[-0.8, -0.51], [0.8, -0.51], [-0.8, 0.51], [0.8, 0.51]]) {
    const bolt = new THREE.Mesh(
      new THREE.CylinderGeometry(0.018, 0.018, 0.009, 20),
      new THREE.MeshBasicMaterial({ color: ACCENT }),
    );
    bolt.position.set(x, 0.008, z);
    platform.add(bolt);
  }

  // --- A / B markers and the carried box ---------------------------------
  const pandaToThree = ([x, y, z]) => new THREE.Vector3(x, z, -y);
  const homeA = pandaToThree(pandaMotion.homeA);
  const homeB = pandaToThree(pandaMotion.homeB);

  function markerTexture(letter, color) {
    const markerCanvas = document.createElement('canvas');
    markerCanvas.width = markerCanvas.height = 256;
    const context = markerCanvas.getContext('2d');
    context.clearRect(0, 0, 256, 256);
    context.strokeStyle = color;
    context.lineWidth = 10;
    context.beginPath();
    context.arc(128, 128, 92, 0, Math.PI * 2);
    context.stroke();
    context.fillStyle = color;
    context.font = '800 76px Inter, sans-serif';
    context.textAlign = 'center';
    context.textBaseline = 'middle';
    context.fillText(letter, 128, 133);
    const texture = new THREE.CanvasTexture(markerCanvas);
    texture.colorSpace = THREE.SRGBColorSpace;
    return texture;
  }

  for (const [position, letter, color] of [[homeA, 'A', '#2563eb'], [homeB, 'B', '#0891b2']]) {
    const marker = new THREE.Mesh(
      new THREE.PlaneGeometry(0.18, 0.18),
      new THREE.MeshBasicMaterial({
        map: markerTexture(letter, color), transparent: true, depthWrite: false,
      }),
    );
    marker.rotation.x = -Math.PI / 2;
    marker.position.set(position.x, 0.004, position.z);
    motionRoot.add(marker);
  }

  const cubeSize = pandaMotion.cubeSize;
  const cubeGeometry = new THREE.BoxGeometry(cubeSize, cubeSize, cubeSize, 2, 2, 2);
  const cube = new THREE.Mesh(cubeGeometry, new THREE.MeshStandardMaterial({
    color: ACCENT, roughness: 0.64, metalness: 0.03,
  }));
  cube.add(new THREE.LineSegments(
    new THREE.EdgesGeometry(cubeGeometry),
    new THREE.LineBasicMaterial({ color: 0xffffff, transparent: true, opacity: 0.86 }),
  ));
  cube.position.copy(homeA);
  motionRoot.add(cube);

  const heldPosition = new THREE.Vector3();
  function updateObjectState(sample) {
    if (sample.carrying && toolTip) {
      // Follow the tool tip exactly but stay upright relative to the table, so
      // the wrist rotation cannot twist the box as it is set down.
      if (cube.parent !== motionRoot) motionRoot.attach(cube);
      toolTip.getWorldPosition(heldPosition);
      motionRoot.worldToLocal(heldPosition);
      cube.position.copy(heldPosition);
      cube.quaternion.identity();
      return;
    }
    const target = sample.restingAtB ? homeB : homeA;
    if (cube.parent !== motionRoot) motionRoot.add(cube);
    cube.position.copy(target);
    cube.quaternion.identity();
  }

  // --- sizing ------------------------------------------------------------
  function resize() {
    const rect = canvas.getBoundingClientRect();
    if (!rect.width || !rect.height) return;
    renderer.setSize(rect.width, rect.height, false);
    camera.aspect = rect.width / Math.max(1, rect.height);
    camera.updateProjectionMatrix();
  }
  const observer = new ResizeObserver(resize);
  observer.observe(canvas);
  resize();

  // --- load, then run ----------------------------------------------------
  const start = performance.now();
  function animate() {
    if (disposed) return;
    const elapsed = (performance.now() - start) / 1000;
    const phase = reduceMotion ? 0 : elapsed % pandaMotion.duration;
    const sample = sampleCycle(phase);
    joints.forEach((pivot, index) => { pivot.rotation.z = sample.pose[index]; });

    if (leftFinger && rightFinger) {
      // Inner faces stop at the box sides, never inside the box.
      const gap = THREE.MathUtils.lerp(cubeSize / 2 + 0.00015, 0.04, sample.grip);
      leftFinger.position.set(0, gap, 0.0584);
      rightFinger.position.set(0, -gap, 0.0584);
    }

    updateObjectState(sample);
    camera.lookAt(0.06, 0.4, 0);
    renderer.render(scene, camera);
    frame = requestAnimationFrame(animate);
  }

  Promise.all([
    loadPart('link0'), loadPart('link1'), loadPart('link2'), loadPart('link3'),
    loadPart('link4'), loadPart('link5'), loadPart('link6'), loadPart('link7'),
    loadPart('hand'), loadPart('finger'),
  ]).then((parts) => {
    if (disposed) return;
    robotZUp.add(parts[0]);
    const j1 = addJoint(robotZUp, [0, 0, 0.333], [0, 0, 0], parts[1]);
    const j2 = addJoint(j1, [0, 0, 0], [-Math.PI / 2, 0, 0], parts[2]);
    const j3 = addJoint(j2, [0, -0.316, 0], [Math.PI / 2, 0, 0], parts[3]);
    const j4 = addJoint(j3, [0.0825, 0, 0], [Math.PI / 2, 0, 0], parts[4]);
    const j5 = addJoint(j4, [-0.0825, 0.384, 0], [-Math.PI / 2, 0, 0], parts[5]);
    const j6 = addJoint(j5, [0, 0, 0], [Math.PI / 2, 0, 0], parts[6]);
    const j7 = addJoint(j6, [0.088, 0, 0], [Math.PI / 2, 0, 0], parts[7]);

    const flange = new THREE.Group();
    flange.position.z = 0.107;
    j7.add(flange);
    const handFrame = new THREE.Group();
    handFrame.rotation.z = -Math.PI / 4;
    flange.add(handFrame);
    handFrame.add(parts[8]);

    leftFinger = new THREE.Group();
    rightFinger = new THREE.Group();
    handFrame.add(leftFinger, rightFinger);
    leftFinger.add(parts[9]);
    // Mirror in the hand's Z-up frame, outside the GLB's Y-up conversion.
    rightFinger.rotation.z = Math.PI;
    rightFinger.add(parts[9].clone(true));

    toolTip = new THREE.Group();
    toolTip.position.z = 0.1034;
    handFrame.add(toolTip);

    resize();
    onReady?.();
    animate();
  }).catch((error) => {
    if (!disposed) onError?.(error);
  });

  return function dispose() {
    disposed = true;
    cancelAnimationFrame(frame);
    observer.disconnect();
    scene.traverse((object) => {
      if (object.isMesh || object.isLineSegments) {
        object.geometry?.dispose();
        const material = object.material;
        if (Array.isArray(material)) material.forEach((item) => item.dispose());
        else material?.dispose();
      }
    });
    renderer.dispose();
  };
}
