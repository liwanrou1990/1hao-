// SPDX-License-Identifier: MIT
pragma solidity ^0.8.28;

interface ISpotPair {
    function spotPrice0() external view returns (uint256); // token0 price in token1, 1e18
    function getReserves() external view returns (uint112 r0, uint112 r1);
}

interface IMintableToken {
    function transfer(address to, uint256 amount) external returns (bool);
    function transferFrom(address from, address to, uint256 amount) external returns (bool);
    function balanceOf(address) external view returns (uint256);
}

/// @title VulnOracleVault  (RESEARCH TARGET — intentionally vulnerable)
/// @notice Reconstruction of the X100 collateral vault for authorized local-chain
///         auditing. DO NOT DEPLOY.
///
///   [VULN-5] DEX liquidity-pool / spot-price oracle manipulation — the vault
///            values deposited collateral (token0) using the AMM's INSTANTANEOUS
///            spot price. No TWAP, no manipulation-resistant oracle. An attacker
///            inflates the spot price with a large (often flash-loaned) swap, then
///            borrows far more payout token than the collateral is worth.
contract VulnOracleVault {
    ISpotPair public immutable pair;
    IMintableToken public immutable collateral; // token0
    IMintableToken public immutable payout;     // token1 (vault reserves)

    mapping(address => uint256) public collateralOf;
    mapping(address => uint256) public debtOf;

    uint256 public constant LTV_BPS = 8000; // 80%

    constructor(ISpotPair _pair, IMintableToken _collateral, IMintableToken _payout) {
        pair = _pair;
        collateral = _collateral;
        payout = _payout;
    }

    function deposit(uint256 amount) external {
        collateral.transferFrom(msg.sender, address(this), amount);
        collateralOf[msg.sender] += amount;
    }

    // =========================================================================
    // [VULN-5] DEX SPOT-PRICE ORACLE MANIPULATION
    // Collateral is valued at pair.spotPrice0() — a single-block manipulable number.
    // =========================================================================
    function maxBorrow(address user) public view returns (uint256) {
        uint256 price = pair.spotPrice0();                 // BUG: instantaneous spot price
        uint256 value = (collateralOf[user] * price) / 1e18;
        return (value * LTV_BPS) / 10000;
    }

    function borrow(uint256 amount) external {
        require(debtOf[msg.sender] + amount <= maxBorrow(msg.sender), "undercollateralized");
        debtOf[msg.sender] += amount;
        require(payout.transfer(msg.sender, amount), "payout");
    }
}
