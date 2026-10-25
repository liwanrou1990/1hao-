// SPDX-License-Identifier: MIT
pragma solidity ^0.8.28;

interface ICumulativePair {
    function currentCumulativePrices() external view returns (uint256 cum0, uint256 cum1, uint32 ts);
}

/// @title SafeTWAPOracle  (REMEDIATED)
/// @notice Fixes [VULN-5]: values collateral with a time-weighted average price
///         accumulated over a minimum window (PERIOD). A single-transaction swap
///         cannot move the published average, so flash-loan price manipulation
///         is neutralized. Uniswap-V2-style fixed-window TWAP.
contract SafeTWAPOracle {
    ICumulativePair public immutable pair;
    uint256 public constant PERIOD = 1800; // 30 min minimum window

    uint256 public priceCumulativeLast;
    uint32 public timestampLast;
    uint256 public price0Average; // 1e18

    constructor(ICumulativePair _pair) {
        pair = _pair;
        (uint256 cum0,, uint32 ts) = _pair.currentCumulativePrices();
        priceCumulativeLast = cum0;
        timestampLast = ts;
    }

    /// @notice Publish a new TWAP; reverts unless at least PERIOD has elapsed.
    function update() external {
        (uint256 cum0,, uint32 ts) = pair.currentCumulativePrices();
        uint32 elapsed = ts - timestampLast;
        require(elapsed >= PERIOD, "period not elapsed");
        price0Average = (cum0 - priceCumulativeLast) / elapsed;
        priceCumulativeLast = cum0;
        timestampLast = ts;
    }

    /// @notice Last published time-weighted price of token0 in token1 (1e18).
    function consult() external view returns (uint256) {
        return price0Average;
    }
}
