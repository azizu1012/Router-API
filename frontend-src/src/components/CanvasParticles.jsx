import React, { useEffect, useRef } from 'react';

export default function CanvasParticles() {
  const canvasRef = useRef(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    
    let W = canvas.width = window.innerWidth;
    let H = canvas.height = window.innerHeight;

    // Twinkling stars for Dark mode
    const stars = Array.from({ length: 90 }, () => ({
      x: Math.random() * W,
      y: Math.random() * H,
      r: Math.random() * 1.3 + 0.4,
      isCross: Math.random() < 0.2,
      alpha: Math.random(),
      phase: Math.random() * Math.PI * 2,
      speed: Math.random() * 0.015 + 0.005,
    }));

    // Shooting stars
    const shootingStars = [];

    // Helper to create flower particle
    const createFlower = (x, y) => {
      const remainingPetals = [0, 1, 2, 3, 4];
      return {
        type: 'flower',
        x: x ?? Math.random() * W,
        y: y ?? (Math.random() * H - H - 100),
        r: Math.random() * 5 + 4.5,
        vy: Math.random() * 0.5 + 0.45,
        vx: (Math.random() - 0.5) * 0.3,
        angle: Math.random() * Math.PI * 2,
        spinSpeed: (Math.random() - 0.5) * 0.015,
        phase: Math.random() * Math.PI * 2,
        phaseSpeed: Math.random() * 0.01 + 0.005,
        alpha: Math.random() * 0.3 + 0.7,
        remainingPetals,
        lastShedTime: Date.now() + Math.random() * 800,
        shedInterval: Math.random() * 1200 + 800,
      };
    };

    // Helper to create individual petal particle
    const createPetal = (x, y, vx, vy, size) => ({
      type: 'petal',
      x: x,
      y: y,
      r: size ?? (Math.random() * 3.5 + 2.5),
      vy: vy ?? (Math.random() * 0.5 + 0.3),
      vx: vx ?? (Math.random() - 0.5) * 0.3,
      angle: Math.random() * Math.PI * 2,
      spinSpeed: (Math.random() - 0.5) * 0.04,
      phase: Math.random() * Math.PI * 2,
      phaseSpeed: Math.random() * 0.025 + 0.01,
      alpha: Math.random() * 0.4 + 0.6,
    });

    const petals = [];
    // Initialize with a mix of flowers and petals spread across the screen
    for (let i = 0; i < 60; i++) {
      if (Math.random() < 0.4) {
        petals.push(createFlower(Math.random() * W, Math.random() * H));
      } else {
        petals.push(createPetal(Math.random() * W, Math.random() * H));
      }
    }

    // Floating pink sparkles for Sakura mode
    const sparkles = Array.from({ length: 30 }, () => ({
      x: Math.random() * W,
      y: Math.random() * H,
      r: Math.random() * 1.6 + 0.6,
      vy: -(Math.random() * 0.4 + 0.1),
      vx: Math.random() * 0.4 - 0.2,
      phase: Math.random() * Math.PI * 2,
      phaseSpeed: Math.random() * 0.02 + 0.01,
      alpha: Math.random() * 0.5 + 0.3,
    }));

    // Floating clouds for Light mode
    const clouds = [
      { x: W * 0.1, y: H * 0.2, speed: 0.16, size: 55, opacity: 0.5 },
      { x: W * 0.45, y: H * 0.12, speed: 0.08, size: 85, opacity: 0.6 },
      { x: W * 0.8, y: H * 0.25, speed: 0.12, size: 65, opacity: 0.48 },
      { x: W * 0.25, y: H * 0.4, speed: 0.06, size: 70, opacity: 0.38 },
      { x: W * 0.65, y: H * 0.35, speed: 0.1, size: 50, opacity: 0.45 },
    ];

    // Pre-cached emoji bitmaps for fast drawImage rendering (avoid costly fillText per-frame)
    const emojiCache = {};
    const getEmojiCanvas = (emoji, size) => {
      const key = emoji + size;
      if (emojiCache[key]) return emojiCache[key];
      const oc = document.createElement('canvas');
      oc.width = oc.height = size;
      const octx = oc.getContext('2d');
      octx.font = `${Math.floor(size * 0.75)}px sans-serif`;
      octx.textAlign = 'center';
      octx.textBaseline = 'middle';
      octx.fillText(emoji, size / 2, size / 2);
      emojiCache[key] = oc;
      return oc;
    };

    // Dynamic arrays for spawned easter eggs
    const customParticles = [];

    // State multipliers triggered by custom events
    let sunSpinMultiplier = 1.0;
    let sunSpinExpiry = 0;
    let blizzardExpiry = 0;
    let discoActive = false;
    let discoTimeoutId = null;

    // ─── Custom Event Listeners ───
    const handleSpawnParticles = (e) => {
      const { type, x, y } = e.detail || {};
      const targetX = x || W / 2;
      const targetY = y || H / 2;

      if (type === 'money') {
        const moneyEmojis = ['💸', '💵', '💰', '💲'];
        for (let i = 0; i < 10; i++) {
          const emoji = moneyEmojis[Math.floor(Math.random() * moneyEmojis.length)];
          const sz = Math.floor((Math.random() * 0.5 + 0.7) * 28);
          customParticles.push({
            type: 'emoji',
            bmp: getEmojiCanvas(emoji, sz),
            sz,
            x: targetX,
            y: targetY,
            vx: (Math.random() - 0.5) * 8,
            vy: -(Math.random() * 6 + 5),
            alpha: 1.0,
            rotation: Math.random() * Math.PI * 2,
            spin: (Math.random() - 0.5) * 0.12,
            gravity: 0.18
          });
        }
      } else if (type === 'sparkles') {
        // Sparkles for Gemini search trigger
        for (let i = 0; i < 18; i++) {
          customParticles.push({
            type: 'sparkle',
            x: targetX,
            y: targetY,
            vx: (Math.random() - 0.5) * 6,
            vy: (Math.random() - 0.5) * 6,
            alpha: 1.0,
            color: Math.random() < 0.5 ? '#f59e0b' : '#a855f7',
            r: Math.random() * 2 + 1.2,
            decay: Math.random() * 0.02 + 0.015
          });
        }
      } else if (type === 'heart') {
        const heartEmojis = ['💖', '🌸', '💘', '💕'];
        for (let i = 0; i < 8; i++) {
          const emoji = heartEmojis[Math.floor(Math.random() * heartEmojis.length)];
          const sz = Math.floor((Math.random() * 0.4 + 0.7) * 26);
          customParticles.push({
            type: 'emoji',
            bmp: getEmojiCanvas(emoji, sz),
            sz,
            x: targetX,
            y: targetY,
            vx: (Math.random() - 0.5) * 6,
            vy: -(Math.random() * 5 + 3),
            alpha: 1.0,
            rotation: Math.random() * Math.PI * 2,
            spin: (Math.random() - 0.5) * 0.08,
            gravity: 0.12
          });
        }
      } else if (type === 'animal') {
        const animalEmojis = ['🐱', '🐶', '🦄', '🐹'];
        for (let i = 0; i < 8; i++) {
          const emoji = animalEmojis[Math.floor(Math.random() * animalEmojis.length)];
          const sz = Math.floor((Math.random() * 0.4 + 0.8) * 28);
          customParticles.push({
            type: 'emoji',
            bmp: getEmojiCanvas(emoji, sz),
            sz,
            x: targetX,
            y: targetY,
            vx: (Math.random() - 0.5) * 10,
            vy: -(Math.random() * 7 + 4),
            alpha: 1.0,
            rotation: Math.random() * Math.PI * 2,
            spin: (Math.random() - 0.5) * 0.1,
            gravity: 0.2
          });
        }
      }
    };

    const handleBackEvent = (e) => {
      const { name } = e.detail || {};
      if (name === 'blizzard') {
        blizzardExpiry = Date.now() + 8000; // 8s blossom windstorm
      } else if (name === 'meteor-shower') {
        // Spawn waves of meteors
        for (let i = 0; i < 40; i++) {
          shootingStars.push({
            x: Math.random() * W * 0.7,
            y: -Math.random() * 800 - 50,
            len: Math.random() * 110 + 70,
            speed: Math.random() * 12 + 10,
            alpha: Math.random() * 0.6 + 0.4,
          });
        }
      } else if (name === 'sun-super-spin') {
        sunSpinExpiry = Date.now() + 5000; // 5s solar spinout
      } else if (name === 'disco') {
        if (discoTimeoutId) clearTimeout(discoTimeoutId);
        if (!discoActive) {
          discoActive = true;
          discoTimeoutId = setTimeout(() => {
            discoActive = false;
            discoTimeoutId = null;
          }, 15000); // 15s auto-deactivation
        } else {
          discoActive = false;
          discoTimeoutId = null;
        }
      }
    };

    window.addEventListener('spawn-custom-particles', handleSpawnParticles);
    window.addEventListener('trigger-back-event', handleBackEvent);

    let sunClicks = 0;
    let moonClicks = 0;
    const handleWindowClick = (e) => {
      const activeTheme = document.documentElement.getAttribute('data-theme') || 'dark';
      const W = window.innerWidth;
      const targetX = W - 140;
      const targetY = 120;
      const dist = Math.hypot(e.clientX - targetX, e.clientY - targetY);

      if (dist < 45) {
        if (activeTheme === 'light') {
          sunClicks++;
          if (sunClicks >= 5) {
            sunClicks = 0;
            sunSpinExpiry = Date.now() + 8000; // spin for 8s
            window.dispatchEvent(new CustomEvent('egg-unlocked-event', { detail: { id: 'sun' } }));
          } else {
            // Spawn solar sparkles
            for (let i = 0; i < 12; i++) {
              customParticles.push({
                type: 'sparkle',
                x: targetX,
                y: targetY,
                vx: (Math.random() - 0.5) * 8,
                vy: (Math.random() - 0.5) * 8,
                alpha: 1.0,
                color: '#fb923c',
                r: Math.random() * 2.5 + 1.5,
                decay: Math.random() * 0.025 + 0.015
              });
            }
          }
        } else if (activeTheme === 'dark') {
          moonClicks++;
          if (moonClicks >= 5) {
            moonClicks = 0;
            // Spawn meteor storm
            for (let i = 0; i < 40; i++) {
              shootingStars.push({
                x: Math.random() * W * 0.7,
                y: -Math.random() * 800 - 50,
                len: Math.random() * 110 + 70,
                speed: Math.random() * 12 + 10,
                alpha: Math.random() * 0.6 + 0.4,
              });
            }
            window.dispatchEvent(new CustomEvent('egg-unlocked-event', { detail: { id: 'moon' } }));
          } else {
            // Spawn lunar sparkles
            for (let i = 0; i < 12; i++) {
              customParticles.push({
                type: 'sparkle',
                x: targetX,
                y: targetY,
                vx: (Math.random() - 0.5) * 8,
                vy: (Math.random() - 0.5) * 8,
                alpha: 1.0,
                color: '#818cf8',
                r: Math.random() * 2.5 + 1.5,
                decay: Math.random() * 0.025 + 0.015
              });
            }
          }
        }
      }
    };
    window.addEventListener('click', handleWindowClick);

    function drawSakuraFlower(ctx, x, y, r, angle, alpha, remainingPetals = [0, 1, 2, 3, 4]) {
      ctx.save();
      ctx.translate(x, y);
      ctx.rotate(angle);
      
      // Draw only the petals that are still attached
      remainingPetals.forEach(petalIndex => {
        ctx.save();
        ctx.rotate((petalIndex * 2 * Math.PI) / 5);
        
        ctx.beginPath();
        ctx.moveTo(0, 0);
        ctx.bezierCurveTo(-r * 0.5, -r * 0.5, -r * 0.7, -r * 1.5, 0, -r * 2.0);
        ctx.bezierCurveTo(r * 0.7, -r * 1.5, r * 0.5, -r * 0.5, 0, 0);
        
        const grad = ctx.createLinearGradient(0, 0, 0, -r * 2.0);
        grad.addColorStop(0, `rgba(244, 63, 94, ${alpha * 0.75})`);
        grad.addColorStop(0.6, `rgba(255, 115, 186, ${alpha * 0.95})`);
        grad.addColorStop(1, `rgba(255, 204, 213, ${alpha})`);
        
        ctx.fillStyle = grad;
        ctx.fill();
        
        // Center line detail for realistic petal texture
        ctx.beginPath();
        ctx.moveTo(0, 0);
        ctx.lineTo(0, -r * 1.4);
        ctx.strokeStyle = `rgba(255, 255, 255, ${alpha * 0.5})`;
        ctx.lineWidth = 0.5;
        ctx.stroke();
        
        ctx.restore();
      });
      
      // Yellow glowing stamens center (only draw if there is at least one petal left)
      if (remainingPetals.length > 0) {
        ctx.beginPath();
        ctx.arc(0, 0, r * 0.35, 0, Math.PI * 2);
        ctx.fillStyle = `rgba(253, 224, 71, ${alpha * 0.9})`;
        ctx.fill();
      }
      
      ctx.restore();
    }

    let animationFrameId;
    let time = 0;

    function draw() {
      time += 0.015;
      ctx.clearRect(0, 0, W, H);
      
      const activeTheme = document.documentElement.getAttribute('data-theme') || 'dark';

      // ─── Rainbow Disco party mode (Overlays on any theme) ───
      if (discoActive) {
        ctx.save();
        ctx.translate(W / 2, H / 2);
        ctx.rotate(time * 0.15);
        for (let i = 0; i < 12; i++) {
          const angle = (i * Math.PI) / 6;
          const hue = (i * 30 + time * 80) % 360;
          ctx.fillStyle = `hsla(${hue}, 85%, 55%, 0.045)`;
          ctx.beginPath();
          ctx.moveTo(0, 0);
          ctx.lineTo(Math.cos(angle - 0.12) * W * 1.5, Math.sin(angle - 0.12) * H * 1.5);
          ctx.lineTo(Math.cos(angle + 0.12) * W * 1.5, Math.sin(angle + 0.12) * H * 1.5);
          ctx.closePath();
          ctx.fill();
        }
        ctx.restore();
      }

      // ─── LIGHT THEME (Pulsing Sun, Sunrays & Volumetric Clouds) ───
      if (activeTheme === 'light') {
        const sunX = W - 140;
        const sunY = 120;
        
        // Fast spin multiplier if solar spin is active
        const isSunSpin = Date.now() < sunSpinExpiry;
        sunSpinMultiplier = isSunSpin ? 15.0 : 1.0;
        
        const sunPulse = Math.sin(time * (isSunSpin ? 6.0 : 0.8)) * (isSunSpin ? 6 : 3);
        const sunRadius = (isSunSpin ? 42 : 38) + sunPulse;

        // Draw Rotating Sun Rays
        ctx.save();
        ctx.translate(sunX, sunY);
        ctx.rotate(time * 0.04 * sunSpinMultiplier);
        ctx.fillStyle = isSunSpin ? 'rgba(251, 146, 60, 0.04)' : 'rgba(251, 146, 60, 0.015)';
        for (let r = 0; r < 8; r++) {
          ctx.beginPath();
          ctx.moveTo(0, 0);
          ctx.lineTo(Math.cos(r * Math.PI / 4 - 0.08) * 450, Math.sin(r * Math.PI / 4 - 0.08) * 450);
          ctx.lineTo(Math.cos(r * Math.PI / 4 + 0.08) * 450, Math.sin(r * Math.PI / 4 + 0.08) * 450);
          ctx.closePath();
          ctx.fill();
        }
        ctx.restore();

        // Draw Golden Sun
        ctx.save();
        ctx.beginPath();
        ctx.arc(sunX, sunY, sunRadius, 0, Math.PI * 2);
        const grad = ctx.createRadialGradient(sunX, sunY, 5, sunX, sunY, sunRadius + 20);
        grad.addColorStop(0, 'rgba(254, 240, 138, 0.95)');
        grad.addColorStop(0.3, isSunSpin ? 'rgba(239, 68, 68, 0.8)' : 'rgba(253, 224, 71, 0.7)');
        grad.addColorStop(0.7, 'rgba(251, 146, 60, 0.3)');
        grad.addColorStop(1, 'rgba(251, 146, 60, 0)');
        ctx.fillStyle = grad;
        ctx.fill();
        ctx.restore();

        // Draw and update drifting volumetric clouds
        clouds.forEach(c => {
          c.x += c.speed;
          if (c.x - c.size * 2 > W) {
            c.x = -c.size * 3;
            c.y = Math.random() * H * 0.5 + 50;
          }

          ctx.save();
          const cloudGrad = ctx.createLinearGradient(c.x, c.y - c.size, c.x, c.y + c.size * 0.8);
          cloudGrad.addColorStop(0, `rgba(255, 255, 255, ${c.opacity})`);
          cloudGrad.addColorStop(0.7, `rgba(244, 245, 249, ${c.opacity * 0.95})`);
          cloudGrad.addColorStop(1, `rgba(219, 227, 244, ${c.opacity * 0.7})`);
          ctx.fillStyle = cloudGrad;
          
          ctx.beginPath();
          ctx.arc(c.x, c.y, c.size, 0, Math.PI * 2);
          ctx.arc(c.x + c.size * 0.65, c.y - c.size * 0.35, c.size * 0.85, 0, Math.PI * 2);
          ctx.arc(c.x + c.size * 1.3, c.y, c.size * 0.75, 0, Math.PI * 2);
          ctx.rect(c.x, c.y - c.size * 0.1, c.size * 1.3, c.size * 0.65);
          ctx.fill();
          ctx.restore();
        });
      }

      // ─── SAKURA THEME (Falling 3D Petals & Glowing Blossom Breeze) ───
      else if (activeTheme === 'valentine') {
        const isBlizzard = Date.now() < blizzardExpiry;
        const currentPetalsLimit = isBlizzard ? 180 : 40;

        // Draw and update small blossom sparkles rising up (glow simulation via concentric arcs)
        sparkles.forEach(s => {
          s.y += s.vy;
          s.phase += s.phaseSpeed;
          s.x += s.vx + Math.sin(s.phase) * (isBlizzard ? 0.6 : 0.25);
          
          if (s.y < -10) {
            s.y = H + 10;
            s.x = Math.random() * W;
          }

          const currentAlpha = s.alpha * (0.3 + 0.7 * Math.abs(Math.sin(time * 0.5 + s.phase)));
          
          // Outer glow circle
          ctx.fillStyle = `rgba(255, 105, 180, ${currentAlpha * 0.18})`;
          ctx.beginPath();
          ctx.arc(s.x, s.y, s.r * 2.5, 0, Math.PI * 2);
          ctx.fill();
          
          // Inner bright circle
          ctx.fillStyle = `rgba(255, 182, 193, ${currentAlpha})`;
          ctx.beginPath();
          ctx.arc(s.x, s.y, s.r, 0, Math.PI * 2);
          ctx.fill();
        });

        // Update and draw falling sakura flowers and petals
        const nextPetals = [];
        const spawnedPetals = [];

        petals.forEach(p => {
          // Update physics
          p.phase += p.phaseSpeed * (isBlizzard ? 1.8 : 1.0);
          p.angle += p.spinSpeed * (isBlizzard ? 2.5 : 1.0);

          if (p.type === 'flower') {
            p.y += p.vy * (isBlizzard ? 2.2 : 1.0);
            p.x += p.vx + (isBlizzard ? 2.0 : 0.1) + Math.sin(p.phase) * 0.15;

            // Check if it's time to shed a petal
            const now = Date.now();
            if (now - p.lastShedTime > (isBlizzard ? p.shedInterval * 0.5 : p.shedInterval)) {
              if (p.remainingPetals.length > 0) {
                // Remove one petal index randomly
                const removeIdx = Math.floor(Math.random() * p.remainingPetals.length);
                const petalNum = p.remainingPetals.splice(removeIdx, 1)[0];
                
                // Calculate position and velocity of the shed petal
                const petalAngle = (petalNum * 2 * Math.PI) / 5 + p.angle;
                const driftSpeed = Math.random() * 0.6 + 0.3;
                const vx = p.vx + Math.cos(petalAngle) * driftSpeed + (isBlizzard ? 1.0 : 0.1);
                const vy = p.vy + Math.sin(petalAngle) * 0.2 + 0.3;
                
                spawnedPetals.push(createPetal(p.x, p.y, vx, vy, p.r * 0.75));
                p.lastShedTime = now;
              }
            }

            // Keep flower if it still has petals and is on screen
            if (p.remainingPetals.length > 0 && p.y <= H + 20 && p.x >= -30 && p.x <= W + 30) {
              nextPetals.push(p);
              // Draw flower with remaining petals
              drawSakuraFlower(ctx, p.x, p.y, p.r, p.angle, p.alpha, p.remainingPetals);
            }
          } else {
            // Petal physics
            p.y += p.vy * (isBlizzard ? 2.5 : 1.0);
            p.x += p.vx + (isBlizzard ? 2.8 : 0.3) + Math.sin(p.phase) * 0.25;

            if (p.y <= H + 20 && p.x >= -30 && p.x <= W + 30) {
              nextPetals.push(p); // Keep petal if on screen
              // Draw petal
              ctx.save();
              ctx.translate(p.x, p.y);
              ctx.rotate(p.angle);
              const scale3D = Math.abs(Math.cos(p.angle * 1.2));
              ctx.scale(scale3D, 1.0);

              ctx.beginPath();
              ctx.moveTo(0, -p.r * 1.8);
              ctx.quadraticCurveTo(p.r * 1.2, -p.r, p.r * 0.9, p.r * 0.5);
              ctx.quadraticCurveTo(0, p.r * 1.9, 0, p.r * 1.5);
              ctx.quadraticCurveTo(0, p.r * 1.9, -p.r * 0.9, p.r * 0.5);
              ctx.quadraticCurveTo(-p.r * 1.2, -p.r, 0, -p.r * 1.8);

              const grad = ctx.createLinearGradient(0, -p.r * 1.8, 0, p.r * 1.5);
              grad.addColorStop(0, `rgba(255, 204, 213, ${p.alpha})`);
              grad.addColorStop(0.5, `rgba(255, 115, 186, ${p.alpha * 0.95})`);
              grad.addColorStop(1, `rgba(244, 63, 94, ${p.alpha * 0.75})`);

              ctx.fillStyle = grad;
              ctx.fill();

              ctx.beginPath();
              ctx.moveTo(0, -p.r * 1.4);
              ctx.lineTo(0, p.r * 1.2);
              ctx.strokeStyle = `rgba(255, 255, 255, ${p.alpha * 0.55})`;
              ctx.lineWidth = 0.5;
              ctx.stroke();

              ctx.restore();
            }
          }
        });

        const combined = [...nextPetals, ...spawnedPetals];
        if (combined.length > 250) {
          combined.splice(250);
        }

        // Re-populate with new flowers at the top if under the limit
        while (combined.length < currentPetalsLimit) {
          combined.push(createFlower(Math.random() * W, -Math.random() * 100 - 20));
        }

        // Update the petals array in place
        petals.length = 0;
        petals.push(...combined);
      }

      // ─── DARK THEME (Twinkling Stars, Moon Aura & Shooting Stars) ───
      else {
        const moonX = W - 140;
        const moonY = 120;
        const moonPulse = Math.sin(time * 0.4) * 4;

        ctx.save();
        ctx.beginPath();
        ctx.arc(moonX, moonY, 32, 0, Math.PI * 2);
        const moonGlow = ctx.createRadialGradient(moonX, moonY, 10, moonX, moonY, 55 + moonPulse);
        moonGlow.addColorStop(0, 'rgba(254, 240, 138, 0.22)');
        moonGlow.addColorStop(0.5, 'rgba(254, 240, 138, 0.06)');
        moonGlow.addColorStop(1, 'rgba(254, 240, 138, 0)');
        ctx.fillStyle = moonGlow;
        ctx.fill();

        ctx.beginPath();
        ctx.arc(moonX, moonY, 28, -Math.PI * 0.5, Math.PI * 0.5, false);
        ctx.arc(moonX - 9, moonY, 28, Math.PI * 0.5, -Math.PI * 0.5, true);
        ctx.closePath();
        ctx.fillStyle = 'rgba(254, 240, 138, 0.94)';
        
        ctx.fill();
        ctx.restore();

        stars.forEach(s => {
          s.phase += s.speed;
          const alpha = 0.15 + 0.8 * Math.abs(Math.sin(s.phase));
          
          ctx.fillStyle = `rgba(255, 255, 255, ${alpha})`;
          if (s.isCross && alpha > 0.45) {
            ctx.beginPath();
            ctx.moveTo(s.x, s.y - s.r * 2.8);
            ctx.quadraticCurveTo(s.x, s.y, s.x + s.r * 2.8, s.y);
            ctx.quadraticCurveTo(s.x, s.y, s.x, s.y + s.r * 2.8);
            ctx.quadraticCurveTo(s.x, s.y, s.x - s.r * 2.8, s.y);
            ctx.quadraticCurveTo(s.x, s.y, s.x, s.y - s.r * 2.8);
            ctx.fill();
          } else {
            ctx.beginPath();
            ctx.arc(s.x, s.y, s.r, 0, Math.PI * 2);
            ctx.fill();
          }
        });

        // Spawn shooting stars randomly
        if (Math.random() < 0.008 && shootingStars.length < 2) {
          shootingStars.push({
            x: Math.random() * W * 0.5,
            y: 0,
            len: Math.random() * 80 + 60,
            speed: Math.random() * 9 + 8,
            alpha: 1,
          });
        }

        // Draw and update shooting stars
        for (let i = shootingStars.length - 1; i >= 0; i--) {
          const s = shootingStars[i];
          s.x += s.speed;
          s.y += s.speed * 0.6;
          s.alpha -= 0.024;

          if (s.alpha <= 0 || s.x > W || s.y > H) {
            shootingStars.splice(i, 1);
            continue;
          }

          ctx.beginPath();
          const grad = ctx.createLinearGradient(s.x, s.y, s.x - s.len, s.y - s.len * 0.6);
          grad.addColorStop(0, `rgba(255, 255, 255, ${s.alpha})`);
          grad.addColorStop(0.5, `rgba(99, 102, 241, ${s.alpha * 0.55})`);
          grad.addColorStop(1, 'rgba(99, 102, 241, 0)');
          
          ctx.strokeStyle = grad;
          ctx.lineWidth = 1.8;
          ctx.moveTo(s.x, s.y);
          ctx.lineTo(s.x - s.len, s.y - s.len * 0.6);
          ctx.stroke();
        }
      }

      // ─── CUSTOM SPAWNED EASTER EGG PARTICLES ───
      for (let i = customParticles.length - 1; i >= 0; i--) {
        const p = customParticles[i];
        if (p.type === 'emoji') {
          p.x += p.vx;
          p.y += p.vy;
          p.vy += p.gravity;
          p.alpha -= 0.015;
          p.rotation += p.spin;

          if (p.alpha <= 0 || p.y > H + 40) {
            customParticles.splice(i, 1);
            continue;
          }

          // Use pre-cached offscreen canvas for emoji — fast drawImage, no fillText per frame
          ctx.save();
          ctx.globalAlpha = p.alpha;
          ctx.translate(p.x, p.y);
          ctx.rotate(p.rotation);
          ctx.drawImage(p.bmp, -p.sz / 2, -p.sz / 2, p.sz, p.sz);
          ctx.restore();
        } else if (p.type === 'sparkle') {
          p.x += p.vx;
          p.y += p.vy;
          p.vx *= 0.98;
          p.vy *= 0.98;
          p.alpha -= p.decay;

          if (p.alpha <= 0) {
            customParticles.splice(i, 1);
            continue;
          }

          ctx.save();
          ctx.fillStyle = p.color;
          
          // Outer glow circle simulation
          ctx.globalAlpha = p.alpha * 0.22;
          ctx.beginPath();
          ctx.arc(p.x, p.y, p.r * 2.8, 0, Math.PI * 2);
          ctx.fill();

          // Inner core circle
          ctx.globalAlpha = p.alpha;
          ctx.beginPath();
          ctx.arc(p.x, p.y, p.r, 0, Math.PI * 2);
          ctx.fill();
          
          ctx.restore();
        }
      }

      animationFrameId = requestAnimationFrame(draw);
    }

    const handleResize = () => {
      if (!canvas) return;
      W = canvas.width = window.innerWidth;
      H = canvas.height = window.innerHeight;
    };

    window.addEventListener('resize', handleResize);
    draw();

    return () => {
      if (discoTimeoutId) clearTimeout(discoTimeoutId);
      window.removeEventListener('resize', handleResize);
      window.removeEventListener('spawn-custom-particles', handleSpawnParticles);
      window.removeEventListener('trigger-back-event', handleBackEvent);
      window.removeEventListener('click', handleWindowClick);
      cancelAnimationFrame(animationFrameId);
    };
  }, []);

  return <canvas ref={canvasRef} id="particles-canvas" className="fixed top-0 left-0 w-full h-full -z-50 pointer-events-none" style={{ willChange: 'transform' }} />;
}
