/* The Earth: a WGS-84 ellipsoid, a day/night surface shader, an atmosphere shell and a star field.
 *
 * Scene convention. The scene axes ARE the ECEF axes: +Z through the north pole, +X through
 * (0 N, 0 E), +Y through (0 N, 90 E). The globe therefore never rotates -- the sun direction and the
 * stars do, once per frame, from geoframe.js. That is what lets a satellite's (lat, lon, alt) and a
 * trajectory sample from the contract be placed by the same function with no extra bookkeeping.
 *
 * Scene units are KILOMETRES.
 */

import * as THREE from "../../vendor/three/three.module.js";
import { R_E_M, WGS84_B_M, geodeticToEcef } from "./geoframe.js";

export const R_EQ_KM = R_E_M / 1000;
export const R_POL_KM = WGS84_B_M / 1000;

/**
 * An ellipsoid built from the frame code itself.
 *
 * Rather than take a unit sphere and squash it, every vertex goes through geodeticToEcef at zero
 * altitude, so the surface is the same WGS-84 ellipsoid the rest of the project computes on, and the
 * UVs are a plain equirectangular (lon, lat) grid with no seam guessing. Normals are the exact
 * geodetic normal, which for an ellipsoid is just the unit vector of (lat, lon).
 */
export function ellipsoidGeometry(segLon = 192, segLat = 96) {
  const nV = (segLon + 1) * (segLat + 1);
  const pos = new Float32Array(nV * 3);
  const nor = new Float32Array(nV * 3);
  const uv = new Float32Array(nV * 2);
  const idx = new Uint32Array(segLon * segLat * 6);
  const p = [0, 0, 0];

  let k = 0;
  for (let j = 0; j <= segLat; j++) {
    const v = j / segLat;
    const lat = 90 - v * 180;
    for (let i = 0; i <= segLon; i++, k++) {
      const u = i / segLon;
      const lon = -180 + u * 360;
      geodeticToEcef(lat, lon, 0, p);
      pos[k * 3] = p[0] / 1000;
      pos[k * 3 + 1] = p[1] / 1000;
      pos[k * 3 + 2] = p[2] / 1000;

      const cl = Math.cos((lat * Math.PI) / 180);
      nor[k * 3] = cl * Math.cos((lon * Math.PI) / 180);
      nor[k * 3 + 1] = cl * Math.sin((lon * Math.PI) / 180);
      nor[k * 3 + 2] = Math.sin((lat * Math.PI) / 180);

      uv[k * 2] = u;
      uv[k * 2 + 1] = 1 - v;
    }
  }

  let t = 0;
  for (let j = 0; j < segLat; j++) {
    for (let i = 0; i < segLon; i++) {
      const a = j * (segLon + 1) + i;
      const b = a + segLon + 1;
      idx[t++] = a; idx[t++] = b; idx[t++] = a + 1;
      idx[t++] = a + 1; idx[t++] = b; idx[t++] = b + 1;
    }
  }

  const g = new THREE.BufferGeometry();
  g.setAttribute("position", new THREE.BufferAttribute(pos, 3));
  g.setAttribute("normal", new THREE.BufferAttribute(nor, 3));
  g.setAttribute("uv", new THREE.BufferAttribute(uv, 2));
  g.setIndex(new THREE.BufferAttribute(idx, 1));
  g.computeBoundingSphere();
  return g;
}

/* ------------------------------------------------------------------ surface shader
 *
 * Why a hand-written shader instead of MeshStandardMaterial: the lit side has to cross into city
 * lights across a soft terminator, the oceans need a specular highlight the land does not, and the
 * limb needs the air to pick up colour. None of that is a property of a standard material.
 */

const SURFACE_VERT = /* glsl */ `
  #include <common>
  #include <logdepthbuf_pars_vertex>
  varying vec2 vUv;
  varying vec3 vNormal;
  varying vec3 vWorld;
  void main() {
    vUv = uv;
    vNormal = normalize(mat3(modelMatrix) * normal);
    vec4 wp = modelMatrix * vec4(position, 1.0);
    vWorld = wp.xyz;
    gl_Position = projectionMatrix * viewMatrix * wp;
    #include <logdepthbuf_vertex>
  }
`;

const SURFACE_FRAG = /* glsl */ `
  precision highp float;
  #include <common>
  #include <logdepthbuf_pars_fragment>
  uniform sampler2D dayMap;
  uniform sampler2D nightMap;
  uniform sampler2D waterMap;
  uniform sampler2D topoMap;
  uniform vec3 sunDir;        // unit, ECEF
  uniform vec3 cameraPosKm;   // camera in the same (floating-origin) space as vWorld
  uniform float nightGain;
  uniform float bumpScale;
  uniform float texelLon;     // 1.0 / topo width, for the finite-difference bump
  uniform float hazeScaleKm;  // path length for unit optical depth at sea level
  uniform float camAltKm;     // camera altitude, for the density weighting
  uniform vec3 hazeColor;

  varying vec2 vUv;
  varying vec3 vNormal;
  varying vec3 vWorld;

  void main() {
    #include <logdepthbuf_fragment>
    vec3 N = normalize(vNormal);

    // Relief. The topography map is a height field, so two differences of it give a slope, which
    // tilts the normal. Subtle on purpose: at this texture resolution a strong bump reads as noise.
    float hL = texture2D(topoMap, vUv - vec2(texelLon, 0.0)).r;
    float hR = texture2D(topoMap, vUv + vec2(texelLon, 0.0)).r;
    float hD = texture2D(topoMap, vUv - vec2(0.0, texelLon)).r;
    float hU = texture2D(topoMap, vUv + vec2(0.0, texelLon)).r;
    vec3 east = normalize(vec3(-N.y, N.x, 0.0) + vec3(1e-6));
    vec3 north = normalize(cross(N, east));
    N = normalize(N - bumpScale * ((hR - hL) * east + (hU - hD) * north));

    float lambert = dot(N, sunDir);

    // A soft terminator. Earth's is about 0.5 degrees of geometry plus atmospheric scattering, so a
    // narrow smoothstep is closer to the truth than a hard edge and reads better than a wide fade.
    float day = smoothstep(-0.085, 0.12, lambert);

    vec3 dayCol = texture2D(dayMap, vUv).rgb;
    vec3 nightCol = texture2D(nightMap, vUv).rgb;

    // Ocean specular: only where the water mask says water, and only on the lit side.
    vec3 V = normalize(cameraPosKm - vWorld);
    vec3 H = normalize(V + sunDir);
    float water = texture2D(waterMap, vUv).r;
    float spec = pow(max(dot(N, H), 0.0), 48.0) * water * smoothstep(0.0, 0.25, lambert);

    // Brightness. The day side gets a gain above one and a gentle lift in the shadows, because a
    // literal Lambert term off a Blue Marble texture is correct and far too dark to read: most of
    // the real brightness of a sunlit Earth comes from cloud and atmosphere this texture does not
    // carry. The ambient floor keeps the unlit side legible instead of a black hole.
    float shade = max(lambert, 0.0);
    shade = shade * (1.25 - 0.3 * shade) + 0.16;   // lifted mid-tones, rolled-off highlights
    vec3 lit = dayCol * shade * 1.5;
    vec3 col = mix(nightCol * nightGain, lit, day);
    col += vec3(0.68, 0.76, 0.86) * spec * 0.55;

    // Terminator warmth: sunlight through a long slant of atmosphere goes orange, and that band is
    // most of what makes a rendered globe look like a photograph of one.
    float band = smoothstep(0.26, 0.0, abs(lambert - 0.03));
    col += vec3(0.5, 0.26, 0.1) * band * 0.3;

    // Atmospheric pick-up at grazing view angles, warm toward the sun.
    float rim = pow(1.0 - max(dot(N, V), 0.0), 3.0);
    col += mix(vec3(0.13, 0.18, 0.26), vec3(0.5, 0.34, 0.17), smoothstep(-0.2, 0.6, lambert)) * rim * day * 0.65;

    // Aerial perspective.
    //
    // Air scatters, so ground seen through a long slant of it washes toward the colour of the sky,
    // and more so the further away it is. Without this the view from a few kilometres up is a flat
    // slab of one texture colour -- at 4,096 pixels across, 5 km above the ground is well inside a
    // single texel, so there is no detail left to carry the image and it reads as a void. The haze
    // is what the eye actually uses to judge distance down there, and it is real physics rather than
    // invented terrain: we are not drawing detail we do not have, we are drawing the air in front
    // of it.
    // Optical depth is the AIR ALONG THE PATH, not the path length: a single haze distance cannot
    // be right for both a view from orbit and a view from five kilometres up, because almost all of
    // the atmosphere is in the bottom ten. So the path is weighted by the density at its midpoint,
    // with the same 8.5 km scale height the ascent simulator uses. From orbit that factor is
    // essentially zero and the ground stays crisp; from a few kilometres up it is near one and
    // distant ground washes out, which is why the sea looks pale grey-blue from an aeroplane and
    // navy from a satellite.
    float viewKm = distance(cameraPosKm, vWorld);
    float midAltKm = max(0.0, camAltKm * 0.5);
    float tau = (viewKm / hazeScaleKm) * exp(-midAltKm / 8.5);
    float haze = (1.0 - exp(-tau)) * day * (0.35 + 0.65 * max(lambert, 0.0));
    col = mix(col, hazeColor * (0.45 + 0.55 * max(lambert, 0.0)), clamp(haze, 0.0, 0.86));

    gl_FragColor = vec4(col, 1.0);
  }
`;

const ATMO_VERT = /* glsl */ `
  #include <common>
  #include <logdepthbuf_pars_vertex>
  varying vec3 vNormal;
  varying vec3 vWorld;
  void main() {
    vNormal = normalize(mat3(modelMatrix) * normal);
    vec4 wp = modelMatrix * vec4(position, 1.0);
    vWorld = wp.xyz;
    gl_Position = projectionMatrix * viewMatrix * wp;
    #include <logdepthbuf_vertex>
  }
`;

// Drawn on the inside of a slightly larger shell and added to the frame, which is the cheap stand-in
// for scattering: brightest at the limb, brightest toward the sun, gone on the night side.
const ATMO_FRAG = /* glsl */ `
  precision highp float;
  #include <common>
  #include <logdepthbuf_pars_fragment>
  uniform vec3 sunDir;
  uniform vec3 cameraPosKm;
  uniform vec3 tint;
  uniform float strength;
  varying vec3 vNormal;
  varying vec3 vWorld;
  void main() {
    #include <logdepthbuf_fragment>
    vec3 N = normalize(vNormal);
    vec3 V = normalize(cameraPosKm - vWorld);
    float limb = pow(1.0 - abs(dot(N, V)), 2.6);
    float lit = smoothstep(-0.35, 0.3, dot(N, sunDir));
    float a = limb * lit * strength;
    gl_FragColor = vec4(tint * a, a);
  }
`;

/**
 * Build the globe: surface mesh, atmosphere shell and a star field, in one group.
 *
 * Textures load asynchronously; the surface starts as a flat earth-brown so the first frame is never
 * a black hole, and `onProgress` reports as each file arrives.
 */
export function createGlobe(opts = {}) {
  const texBase = opts.texBase ?? "public/textures/";
  const onProgress = opts.onProgress ?? (() => {});
  const group = new THREE.Group();
  group.name = "earth";

  const loader = new THREE.TextureLoader();
  const blank = () => {
    const d = new Uint8Array([40, 32, 24, 255]);
    const t = new THREE.DataTexture(d, 1, 1, THREE.RGBAFormat);
    t.needsUpdate = true;
    return t;
  };

  const uniforms = {
    dayMap: { value: blank() },
    nightMap: { value: blank() },
    waterMap: { value: blank() },
    topoMap: { value: blank() },
    sunDir: { value: new THREE.Vector3(1, 0, 0) },
    cameraPosKm: { value: new THREE.Vector3() },
    nightGain: { value: 1.9 },
    bumpScale: { value: 0.0 },
    texelLon: { value: 1 / 2048 },
    // 32 km of sea-level air is about one optical depth: a clear day's visibility.
    hazeScaleKm: { value: 32 },
    camAltKm: { value: 1e6 },
    hazeColor: { value: new THREE.Color(0.52, 0.65, 0.82) },
  };

  let pending = 0;
  const grab = (file, key, cfg = {}) => {
    pending++;
    loader.load(
      texBase + file,
      (t) => {
        t.colorSpace = cfg.srgb ? THREE.SRGBColorSpace : THREE.NoColorSpace;
        t.wrapS = THREE.RepeatWrapping;
        t.wrapT = THREE.ClampToEdgeWrapping;
        t.anisotropy = opts.anisotropy ?? 4;
        t.generateMipmaps = true;
        t.minFilter = THREE.LinearMipmapLinearFilter;
        uniforms[key].value.dispose?.();
        uniforms[key].value = t;
        if (key === "topoMap") {
          uniforms.texelLon.value = 1 / (t.image?.width || 2048);
          uniforms.bumpScale.value = 0.55; // only switched on once there is real relief to shade
        }
        onProgress({ file, loaded: true, remaining: --pending });
      },
      undefined,
      () => onProgress({ file, loaded: false, remaining: --pending, failed: true }),
    );
  };

  grab("earth_day.jpg", "dayMap", { srgb: true });
  grab("earth_night.jpg", "nightMap", { srgb: true });
  grab("earth_water.png", "waterMap");
  grab("earth_topo.png", "topoMap");

  const surface = new THREE.Mesh(
    ellipsoidGeometry(opts.segLon ?? 192, opts.segLat ?? 96),
    new THREE.ShaderMaterial({
      uniforms,
      vertexShader: SURFACE_VERT,
      fragmentShader: SURFACE_FRAG,
    }),
  );
  surface.name = "earth-surface";
  group.add(surface);

  // 110 km shell: roughly where the visible airglow sits, and it keeps the limb off the surface mesh.
  const atmoUniforms = {
    sunDir: uniforms.sunDir,
    cameraPosKm: uniforms.cameraPosKm,
    tint: { value: new THREE.Color(0.42, 0.62, 0.86) },
    strength: { value: 1.75 },
  };
  const atmo = new THREE.Mesh(
    new THREE.SphereGeometry(1, 96, 64),
    new THREE.ShaderMaterial({
      uniforms: atmoUniforms,
      vertexShader: ATMO_VERT,
      fragmentShader: ATMO_FRAG,
      transparent: true,
      blending: THREE.AdditiveBlending,
      side: THREE.BackSide,
      depthWrite: false,
    }),
  );
  atmo.scale.set(R_EQ_KM + 110, R_EQ_KM + 110, R_POL_KM + 110);
  atmo.name = "atmosphere";
  group.add(atmo);

  return {
    group,
    surface,
    atmosphere: atmo,
    uniforms,
    /** Call once per frame: `sun` is a unit ECEF vector, `camKm` the camera in floating-origin space. */
    update(sun, camKm, camAltKm) {
      uniforms.sunDir.value.set(sun[0], sun[1], sun[2]);
      uniforms.cameraPosKm.value.copy(camKm);
      if (camAltKm != null) uniforms.camAltKm.value = camAltKm;
    },
    setOrigin(originKm) {
      group.position.set(-originKm.x, -originKm.y, -originKm.z);
    },
    dispose() {
      surface.geometry.dispose();
      surface.material.dispose();
      atmo.geometry.dispose();
      atmo.material.dispose();
      for (const u of Object.values(uniforms)) u.value?.dispose?.();
    },
  };
}

/* ------------------------------------------------------------------ stars */

/** Deterministic 32-bit PRNG, so the sky is the same sky on every load and in every browser. */
function mulberry32(seed) {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/**
 * A star field.
 *
 * These are NOT real stars: there is no star catalog in this project and none could be fetched, so
 * the positions are a seeded random draw on the sphere and the brightnesses follow a rough power law
 * so a few stand out. It exists to give the eye a fixed frame to judge rotation against, and the UI
 * says it is decorative. Drawn at a fixed pixel size with depth writing off, so it sits behind
 * everything and never interferes with the depth range.
 */
export function createStars(count = 6200, seed = 20261110) {
  const rnd = mulberry32(seed);
  const pos = new Float32Array(count * 3);
  const size = new Float32Array(count);
  const tint = new Float32Array(count * 3);
  const R = 1; // unit sphere; the shader pushes them to the far plane

  for (let i = 0; i < count; i++) {
    // Uniform on the sphere: z uniform in [-1,1], azimuth uniform.
    const z = 2 * rnd() - 1;
    const a = 2 * Math.PI * rnd();
    const r = Math.sqrt(Math.max(0, 1 - z * z));
    pos[i * 3] = R * r * Math.cos(a);
    pos[i * 3 + 1] = R * r * Math.sin(a);
    pos[i * 3 + 2] = R * z;

    const m = Math.pow(rnd(), 2.6); // most faint, a few bright
    size[i] = 0.9 + 2.3 * m;
    // Slight colour spread around white, warm to cool.
    const w = 0.84 + 0.16 * rnd();
    tint[i * 3] = w + 0.1 * m;
    tint[i * 3 + 1] = w;
    tint[i * 3 + 2] = w + 0.09 * (1 - m);
  }

  const g = new THREE.BufferGeometry();
  g.setAttribute("position", new THREE.BufferAttribute(pos, 3));
  g.setAttribute("aSize", new THREE.BufferAttribute(size, 1));
  g.setAttribute("aTint", new THREE.BufferAttribute(tint, 3));

  const mat = new THREE.ShaderMaterial({
    uniforms: { pixelRatio: { value: 1 }, opacity: { value: 1.0 }, starRadius: { value: 1e6 } },
    vertexShader: /* glsl */ `
      #include <common>
      #include <logdepthbuf_pars_vertex>
      attribute float aSize;
      attribute vec3 aTint;
      uniform float pixelRatio;
      uniform float starRadius;
      varying vec3 vTint;
      void main() {
        vTint = aTint;
        // Direction only, re-projected from the camera every frame, so the field never parallaxes
        // however far the camera travels. It sits just inside the far plane rather than being
        // clamped onto it: a clamped star is at the same depth as everything else at the horizon,
        // and the planet has to be able to pass in front of it.
        vec3 dir = normalize(position);
        gl_Position = projectionMatrix * viewMatrix * vec4(cameraPosition + dir * starRadius, 1.0);
        gl_PointSize = aSize * pixelRatio;
        #include <logdepthbuf_vertex>
      }
    `,
    fragmentShader: /* glsl */ `
      precision mediump float;
      #include <common>
      #include <logdepthbuf_pars_fragment>
      uniform float opacity;
      varying vec3 vTint;
      void main() {
        #include <logdepthbuf_fragment>
        vec2 d = gl_PointCoord - vec2(0.5);
        float a = smoothstep(0.5, 0.08, length(d) * 2.0);
        if (a <= 0.01) discard;
        gl_FragColor = vec4(vTint, a * opacity);
      }
    `,
    transparent: true,
    depthWrite: false,
    depthTest: true,
    blending: THREE.AdditiveBlending,
  });

  const points = new THREE.Points(g, mat);
  points.name = "stars";
  points.frustumCulled = false;
  points.renderOrder = -1000;
  return {
    points,
    material: mat,
    /** Keep the field just inside the far plane, which moves as the camera's range changes. */
    update(camera) {
      mat.uniforms.starRadius.value = camera.far * 0.85;
    },
    dispose: () => { g.dispose(); mat.dispose(); },
  };
}
