// Roblox Bridge lamp bake (editor only). Bakes the Roblox Lit shader's own lamp lighting into the lightmaps:
// the same falloff (RbxDistanceFalloff), spot cone, colour x intensity, N.L and hard shadows that start at the
// light's shadow near plane, so the baked lightmap shows exactly what the real-time lamps showed.
// Driven by Editor/RobloxLampBaker.cs.
Shader "Hidden/Roblox Bridge/Lamp Bake"
{
    SubShader
    {
        Cull Off ZWrite Off ZTest Always

        // 0: unwrap a renderer into its lightmap rectangle -> world position (w = covered) and normal
        Pass
        {
            CGPROGRAM
            #pragma vertex vert
            #pragma fragment frag
            #include "UnityCG.cginc"
            float4 _LmST, _Tile;   // _Tile: lightmap UV origin (xy) and size (zw) of the tile being baked
            struct v2f { float4 pos : SV_POSITION; float3 wpos : TEXCOORD0; float3 wnrm : TEXCOORD1; };
            struct mrt { float4 p : SV_Target0; float4 n : SV_Target1; };
            v2f vert(float4 vertex : POSITION, float3 normal : NORMAL, float2 uv2 : TEXCOORD1)
            {
                v2f o;
                float2 uv = (uv2 * _LmST.xy + _LmST.zw - _Tile.xy) / _Tile.zw;
                o.pos = float4(uv * 2.0 - 1.0, 0.5, 1.0);
            #if UNITY_UV_STARTS_AT_TOP
                o.pos.y = -o.pos.y;
            #endif
                o.wpos = mul(unity_ObjectToWorld, vertex).xyz;
                o.wnrm = UnityObjectToWorldNormal(normal);
                return o;
            }
            mrt frag(v2f i)
            {
                mrt o;
                o.p = float4(i.wpos, 1.0);
                o.n = float4(normalize(i.wnrm), 1.0);
                return o;
            }
            ENDCG
        }

        // 1: shadow map face: distance from the light
        Pass
        {
            Cull Off ZWrite On ZTest LEqual
            CGPROGRAM
            #pragma vertex vert
            #pragma fragment frag
            #include "UnityCG.cginc"
            float3 _LampPos;
            struct v2f { float4 pos : SV_POSITION; float3 wpos : TEXCOORD0; };
            v2f vert(float4 vertex : POSITION)
            {
                v2f o;
                o.wpos = mul(unity_ObjectToWorld, vertex).xyz;
                o.pos = UnityObjectToClipPos(vertex);
                return o;
            }
            float4 frag(v2f i) : SV_Target { return distance(i.wpos, _LampPos); }
            ENDCG
        }

        // 2: one lamp's light on every covered lightmap texel (added up over all lamps)
        Pass
        {
            Blend One One
            CGPROGRAM
            #pragma vertex vert
            #pragma fragment frag
            #pragma require 2darray
            #include "UnityCG.cginc"
            sampler2D _PosTex, _NrmTex;
            UNITY_DECLARE_TEX2DARRAY(_ShadowTex);
            float4x4 _FaceVP[6];
            float3 _LampPos, _LampColor, _SpotFwd;
            float _Range, _K, _SpotInv, _SpotOff, _IsSpot, _HasShadow, _Bias, _ShadowTexel, _JunctionTol;
            struct v2f { float4 pos : SV_POSITION; float2 uv : TEXCOORD0; };
            v2f vert(uint id : SV_VertexID)
            {
                v2f o;
                float2 uv = float2((id << 1) & 2, id & 2);
                o.pos = float4(uv * 2.0 - 1.0, 0.5, 1.0);
                o.uv = uv;
            #if UNITY_UV_STARTS_AT_TOP
                o.uv.y = 1.0 - o.uv.y;
            #endif
                return o;
            }
            // the Roblox Lit shader's RbxDistanceFalloff (Shaders/RobloxLighting.hlsl), unchanged
            float RbxDistanceFalloff(float d2, float range, float k)
            {
                float start = 0.72 * range;
                float e = max(sqrt(d2) - start, 0.0);
                float t = saturate(e / (range - start));
                return (1.0 - t * t * (3.0 - 2.0 * t)) * k / (k + e * e);
            }
            struct lampOut { float4 c : SV_Target0; float4 d : SV_Target1; };
            lampOut frag(v2f i)
            {
                float4 p = tex2D(_PosTex, i.uv);
                if (p.w < 0.5) discard;
                float3 n = normalize(tex2D(_NrmTex, i.uv).xyz);
                float3 v = _LampPos - p.xyz;
                float d2 = max(dot(v, v), 1e-6);
                if (d2 >= _Range * _Range) discard;
                float3 l = v * rsqrt(d2);
                float ndl = saturate(dot(n, l));
                if (ndl <= 0.0) discard;
                float att = RbxDistanceFalloff(d2, _Range, _K);
                // URP's spot cone with inner angle 0 (= Roblox's cone)
                if (_IsSpot > 0.5) { float c = saturate(dot(_SpotFwd, -l) * _SpotInv + _SpotOff); att *= c * c; }
                if (_HasShadow > 0.5)
                {
                    float3 d = p.xyz - _LampPos;
                    float3 a = abs(d);
                    int face = a.x >= a.y && a.x >= a.z ? (d.x > 0 ? 0 : 1) : (a.y >= a.z ? (d.y > 0 ? 2 : 3) : (d.z > 0 ? 4 : 5));
                    // offset grows with distance: one shadow-map texel covers more surface further from the lamp
                    float dist = sqrt(d2);
                    float texel = dist * _ShadowTexel;
                    float off = _Bias + 1.5 * texel;
                    float3 sp = p.xyz + n * off;
                    float4 c = mul(_FaceVP[face], float4(sp, 1.0));
                    float2 suv = c.xy / c.w * 0.5 + 0.5;
                    float occ = UNITY_SAMPLE_TEX2DARRAY(_ShadowTex, float3(suv, face)).r;
                    // an occluder within _JunctionTol of the surface is geometry the sample point touches or sits
                    // just inside (where two parts meet), not a shadow: lightmappers treat such points as invalid
                    if (occ < distance(sp, _LampPos) - max(off, _JunctionTol)) discard;
                }
                float3 c = _LampColor * att * ndl;
                float lum = dot(c, float3(0.2126, 0.7152, 0.0722));
                lampOut o;
                o.c = float4(c, 0.0);
                o.d = float4(l * lum, lum);   // summed over lamps: luminance-weighted direction to the light
                return o;
            }
            ENDCG
        }

        // 3: dilate: fill empty texels next to covered ones (no dark seams at UV island edges)
        Pass
        {
            CGPROGRAM
            #pragma vertex vert
            #pragma fragment frag
            #include "UnityCG.cginc"
            sampler2D _Src, _Mask;
            float4 _Src_TexelSize;
            struct v2f { float4 pos : SV_POSITION; float2 uv : TEXCOORD0; };
            struct mrt { float4 c : SV_Target0; float4 m : SV_Target1; };
            v2f vert(uint id : SV_VertexID)
            {
                v2f o;
                float2 uv = float2((id << 1) & 2, id & 2);
                o.pos = float4(uv * 2.0 - 1.0, 0.5, 1.0);
                o.uv = uv;
            #if UNITY_UV_STARTS_AT_TOP
                o.uv.y = 1.0 - o.uv.y;
            #endif
                return o;
            }
            mrt frag(v2f i)
            {
                mrt o;
                float m = tex2D(_Mask, i.uv).r;
                o.c = tex2D(_Src, i.uv);
                o.m = m;
                if (m > 0.5) return o;
                float4 sum = 0; float cnt = 0;   // all four channels (the directional map keeps its weight in alpha)
                for (int y = -1; y <= 1; y++)
                for (int x = -1; x <= 1; x++)
                {
                    float2 uv = i.uv + float2(x, y) * _Src_TexelSize.xy;
                    float mm = tex2D(_Mask, uv).r;
                    sum += tex2D(_Src, uv) * mm; cnt += mm;
                }
                if (cnt > 0) { o.c = sum / cnt; o.m = 1; }
                return o;
            }
            ENDCG
        }

        // 4: downsample the supersampled light: average the covered sub-samples (coverage in _Mask)
        Pass
        {
            CGPROGRAM
            #pragma vertex vert
            #pragma fragment frag
            #include "UnityCG.cginc"
            sampler2D _Src, _Mask, _Dir, _Nrm;
            float4 _Src_TexelSize;
            float _SS;
            struct v2f { float4 pos : SV_POSITION; float2 uv : TEXCOORD0; };
            struct mrt4 { float4 c : SV_Target0; float4 m : SV_Target1; float4 d : SV_Target2; float4 n : SV_Target3; };
            v2f vert(uint id : SV_VertexID)
            {
                v2f o;
                float2 uv = float2((id << 1) & 2, id & 2);
                o.pos = float4(uv * 2.0 - 1.0, 0.5, 1.0);
                o.uv = uv;
            #if UNITY_UV_STARTS_AT_TOP
                o.uv.y = 1.0 - o.uv.y;
            #endif
                return o;
            }
            mrt4 frag(v2f i)
            {
                mrt4 o;
                float3 sum = 0; float cnt = 0; float4 dsum = 0; float3 nsum = 0;
                int n = (int)_SS;
                float2 base = i.uv - 0.5 * (_SS - 1) * _Src_TexelSize.xy;
                for (int y = 0; y < n; y++)
                for (int x = 0; x < n; x++)
                {
                    float2 uv = base + float2(x, y) * _Src_TexelSize.xy;
                    float m = tex2D(_Mask, uv).r;
                    sum += tex2D(_Src, uv).rgb * m; cnt += m;
                    dsum += tex2D(_Dir, uv) * m;
                    nsum += tex2D(_Nrm, uv).xyz * m;
                }
                o.c = float4(cnt > 0 ? sum / cnt : 0, cnt > 0 ? 1 : 0);   // alpha = covered
                o.m = cnt > 0 ? 1 : 0;
                o.d = cnt > 0 ? dsum / cnt : 0;
                o.n = float4(cnt > 0 ? nsum / cnt : 0, 0);
                return o;
            }
            ENDCG
        }
    }
}